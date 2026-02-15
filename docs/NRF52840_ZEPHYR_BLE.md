# nRF52840 DK — Native Zephyr BLE Port

Notes from bringing up the MicroPython Zephyr port with BLE on the Nordic
nRF52840 DK (PCA10056). This uses the native Zephyr build system
(`find_package(Zephyr)` via west) rather than the RP2/STM32 ports which compile
Zephyr BLE sources directly into the MicroPython build.

## Build Environment

- **West workspace**: root `~`, manifest at `~/zephyr` (v4.2.0)
- **Toolchain**: GNU ARM (`/opt/arm-gnu-toolchain-14.3.rel1-x86_64-arm-none-eabi/`)
  using `ZEPHYR_TOOLCHAIN_VARIANT=gnuarmemb`. The Zephyr SDK ARM toolchain binaries
  weren't installed so gnuarmemb was used instead.
- **Flash**: openocd 0.12.0 with J-Link interface (nrfjprog not installed)
- **Console**: SEGGER J-Link virtual UART at 115200 baud

### Build Commands

```bash
export ZEPHYR_TOOLCHAIN_VARIANT=gnuarmemb
export GNUARMEMB_TOOLCHAIN_PATH=/opt/arm-gnu-toolchain-14.3.rel1-x86_64-arm-none-eabi
export ZEPHYR_BASE=~/zephyr

west build -b nrf52840dk/nrf52840 \
    -s /home/corona/mpy/zephy_ble/ports/zephyr \
    -d /home/corona/mpy/zephy_ble/ports/zephyr/build \
    -p auto
```

### Flash

```bash
openocd -f interface/jlink.cfg -c "transport select swd" -f target/nrf52.cfg \
    -c "adapter serial 000683828506" \
    -c "init; reset halt; program ports/zephyr/build/zephyr/zephyr.hex verify reset exit"
```

## Key Fixes

### machine.idle(): k_yield() vs k_msleep(1)

`k_yield()` only yields to threads of equal or higher priority. The MicroPython
main thread runs at priority 0 (highest cooperative priority in Zephyr). The BT
long work queue runs at priority 10 (lower). This means `k_yield()` never lets
the BLE stack process long-running operations like ECDH key generation during
SMP pairing.

Fix: replace `k_yield()` with `k_msleep(1)` in `mp_machine_idle()`. The 1ms
sleep suspends the main thread and allows all lower-priority threads to run.

On the RP2/STM32 ports this wasn't an issue because the BLE HCI processing
either runs on a FreeRTOS task or via cooperative polling from the main loop,
not via Zephyr's kernel scheduler.

### ECDH Acceleration (p256-m vs mbedTLS ECP)

Zephyr's default `p256-m` library for P-256 ECDH is extremely slow on Cortex-M4
(~37 seconds for key generation on nRF52840). This caused SMP pairing to
effectively hang even after the `k_msleep` fix above.

Fix: disable p256-m and enable full mbedTLS ECP with NIST fast modular reduction:

```
CONFIG_MBEDTLS_PSA_P256M_DRIVER_ENABLED=n
CONFIG_MBEDTLS_ECP_C=y
CONFIG_MBEDTLS_ECP_DP_SECP256R1_ENABLED=y
CONFIG_MBEDTLS_ECDH_C=y
CONFIG_MBEDTLS_ECP_NIST_OPTIM=y
CONFIG_BT_LONG_WQ_STACK_SIZE=4096
```

This brings ECDH key generation from ~37s to ~181ms. The long work queue stack
needs to be 4096 to accommodate the larger ECP stack usage.

### Deinit Connection Cleanup

On the native Zephyr port, `bt_disable()` is not available (Zephyr doesn't
support disabling the BT stack at runtime). The BLE stack persists across Python
VM soft resets. This means connections and bonds from a previous session remain
active when MicroPython reinitializes.

Added `mp_bt_zephyr_disconnect_all_wait()` which iterates all LE connections,
calls `bt_conn_disconnect()`, and waits up to 1 second for the connection pool
slots to be freed. Called both during normal deinit and when resuming from
SUSPENDED state.

## J-Link Virtual UART Limitations

The nRF52840 DK's console runs over SEGGER J-Link virtual UART at 115200 baud.
This has several implications for the multitest framework:

- **No raw paste protocol**: The virtual UART can't handle the faster raw paste
  mode. Falls back to raw REPL with explicit chunked writes.
- **Upload speed ceiling**: 64 bytes per 10ms chunk (~6.4 KB/s, half wire rate)
  is the fastest reliable transfer rate. 128B/10ms drops characters and causes
  SyntaxError.
- **Script size limit**: Test scripts larger than ~4.5KB take long enough to
  upload that the test framework's internal timeouts can expire before both
  devices have started.
- **Soft reset latency**: `enter_raw_repl` with soft reset takes ~3 seconds on
  this UART, so the overall timeout needs extending to 30s.

A patched multitest wrapper (`/tmp/run_ble_multitests.py`) handles these issues
by monkey-patching `pyboard.Pyboard.exec_raw_no_follow` and
`pyboard.Pyboard.enter_raw_repl` for slow serial paths.

### UART Buffer Size

Increased `UART_BUFSIZE` from 512 to 4096 in `zephyr_getchar.c` to reduce
dropped characters during bulk transfers over J-Link UART.

## BLE Multitest Results

Tests run with Pico W (instance0, peripheral) and nRF52840 DK (instance1,
central). Hardware resets via openocd between each test to clear Zephyr kernel
bond state.

| Test | Result | Notes |
|------|--------|-------|
| ble_gap_advertise | PASS | |
| ble_gap_connect | PASS | |
| ble_gap_pair | PASS | |
| ble_gap_pair_bond | FAIL | bonded flag reports 0 instead of 1 |
| ble_gattc_discover_services | PASS | |
| ble_irq_calls | FAIL | RuntimeError: maximum recursion depth exceeded |
| ble_l2cap | FAIL | ImportError: no module named 'random' |
| ble_subscribe | FAIL | Event ordering + forced notifications limitation |
| ble_characteristic | FAIL | Indicate type detection (known issue #19) |

### Failure Analysis

**ble_gap_pair_bond**: Pairing completes but `bonded` parameter in the
`pairing_complete` callback is always 0. Likely a Zephyr bond storage
configuration issue — needs investigation of `CONFIG_BT_SETTINGS` and flash
storage backend.

**ble_irq_calls**: Stack overflow from recursive BLE event processing.
`CONFIG_MAIN_STACK_SIZE=8192` may not be sufficient for deeply nested callbacks.
May need increasing or the test may need adaptation.

**ble_l2cap**: The `random` module is not included in the native Zephyr port
build. Needs adding to the build config or the test needs a fallback.

**ble_subscribe**: Zephyr only delivers notifications to explicitly subscribed
handles (via CCCD). This is architecturally different from NimBLE which delivers
all notifications regardless of subscription state. Not a bug.

**ble_characteristic**: Known issue #19 — Zephyr's `att_notify()` and
`att_indicate()` both call `bt_gatt_notification()` with identical parameters,
so indications are misclassified as notifications.

## USB CDC (Not Working)

Attempted to route the MicroPython REPL over USB CDC ACM instead of J-Link UART
using a DTS overlay (`nrf52840dk_nrf52840.overlay.usb`). The overlay successfully
redirects the Zephyr console to USB CDC but the REPL breaks — the device
enumerates as a COM port but communication fails. This needs further
investigation; the overlay is checked in but not active.

## Test Device Roles

The nRF52840 DK runs as **central** (instance1) and the Pico W as **peripheral**
(instance0). This is because the Pico W crashes (RP2040 double fault) when
attempting `gap_connect` with `addr_type=1` (random address). Public address
connections work but the multitest framework uses random addresses. Using the
nRF52840 as central avoids this issue.

## Diagnostic Instrumentation

The current `modbluetooth_zephyr.c` diff includes temporary volatile diagnostic
counters (`_smp_diag_*`) and extra `bt_conn_get_info` calls in the pairing
callbacks. These were used to debug the SMP pairing flow via GDB and should be
removed before merging.
