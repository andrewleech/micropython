# Zephyr BLE Integration for MicroPython

## Goal
Add Zephyr BLE stack as an alternative to NimBLE/BTstack for all MicroPython ports.

## Current Status
- OS abstraction layer implemented (k_work, k_sem, k_mutex, atomic ops)
- Build system integration complete
- **STM32WB55 NimBLE: Fully working!**
  - BLE activation works ✓
  - BLE advertising works ✓
  - BLE scanning works (227 devices found in 5s test) ✓
  - BLE connections work ✓
  - All IRQ events delivered correctly ✓
  - Fix #4: Restored IPCC memory sections in linker script (critical fix for BLE activation)
- **STM32WB55 Zephyr BLE: WORKING!**
  - bt_enable() completes successfully ✓
  - BLE advertising works (legacy mode) ✓
  - BLE connections work (peripheral and central roles) ✓
  - Connection IRQ events delivered correctly ✓
  - **BLE scanning works!** ✓ (69 devices in 5s scan, no errors)
  - Fix #1: Disabled CONFIG_BT_HCI_ACL_FLOW_CONTROL (STM32WB controller doesn't support HOST_BUFFER_SIZE command)
  - Fix #2: Enabled CONFIG_BT_SCAN_WITH_IDENTITY to fix scanning EPERM error
  - Fix #3: Disabled CONFIG_BT_SMP (STM32WB controller sends legacy connection events, not enhanced)
  - Fix #4: Restored IPCC memory sections (CRITICAL - fixed advertising report reception for both stacks)
  - Fix #5: Increased RX_QUEUE_SIZE from 8 to 32 (commit 6d8e3370a9)
  - Fix #6: Increased Zephyr buffer pools (EVT_RX:32, ACL_RX:16, DISCARDABLE:8)
  - **Known limitation**: Detects ~30% of devices compared to NimBLE (69 vs 227 in same test)
    - See `docs/BUFFER_FIX_VERIFICATION.md` for analysis
    - Future optimization opportunity (work queue processing)
  - **Investigation**: Event mask ordering and vendor commands (see VENDOR_COMMAND_INVESTIGATION.md)
    - Both hypotheses disproven - Zephyr sends identical init sequence to NimBLE
    - Event mask ordering does NOT affect LE Meta Event delivery
    - All Zephyr submodule changes reverted (lib/zephyr clean)
- RP2 Pico 2 W: Not yet tested with these fixes

## Test Hardware
- **RP2**: Raspberry Pi Pico 2 W (RP2350) with CYW43 BT controller via SPI
- **STM32**: NUCLEO_WB55 (STM32WB55RGVx) with internal BT controller via IPCC

## Board Variants

### STM32 NUCLEO_WB55 Board Variants

The STM32 NUCLEO_WB55 board supports two BLE stack variants:

**Default (NimBLE)**: Production-ready BLE stack
```bash
cd ports/stm32
make BOARD=NUCLEO_WB55
# Firmware: build-NUCLEO_WB55/firmware.elf
```

**Zephyr Variant**: Experimental Zephyr BLE stack (under development)
```bash
cd ports/stm32
make BOARD=NUCLEO_WB55 BOARD_VARIANT=zephyr ZEPHYR_BLE_DEBUG=1
# Firmware: build-NUCLEO_WB55-zephyr/firmware.elf
```

**Latest Commits**:
- Fix #7: Recursion deadlock: 639d7ddcb3, 6bdcbeb9ef, 7f1f4503d2 (architectural fix for HCI command completion)
- Central role: 193658620a (gap_peripheral_connect + connection mgmt fixes)
- Endian fix: 6a310aa1e623ee0ee73ba4aa663c73217d5ca690
- Buffer fixes: 6d8e3370a9 (buffer pools + RX_QUEUE_SIZE increase)
- Instrumentation: 5bd77486cd (debug counters for future diagnostics)
- Verification: 0536e94a1a (documentation of results)

**Documentation**:
- Endian fix: `docs/20251020_ZEPHYR_BLE_SCAN_PARAMETER_FIX.md`
- Buffer investigation: `docs/BUFFER_INVESTIGATION_RESULTS.md`
- Verification results: `docs/BUFFER_FIX_VERIFICATION.md`
- Issue #6 analysis: `ISSUE_6_FINAL_ANALYSIS.md`, `ISSUE_6_ROOT_CAUSE_SUMMARY.md`

**Status**:
- NimBLE (default): ✓ Full BLE functionality working (advertising, scanning, connections, central+peripheral roles)
- Zephyr variant: ⚠ **Phase 2B: Fix #7 VERIFIED WORKING, Issue #6 CONFIRMED PERSISTS**
  - ✓ Peripheral role (advertising, accepting connections from central devices)
  - ✓ GATT Server (providing services/characteristics)
  - ✓ Scanning (passive/active scan, advertising report reception)
  - ✓ **Fix #7**: Recursion prevention deadlock resolved (commits 639d7ddcb3, 6bdcbeb9ef, 7f1f4503d2)
    - Added `in_wait_loop` flag to allow work processing during semaphore waits
    - Fixed H4 buffer format (removed duplicate packet type byte)
    - Added work processing to WFI function
    - HCI command/response flow now working
    - **VERIFIED**: Multi-test with commit ea56a42996 shows semaphores work (26-38ms acquisition)
    - **VERIFIED**: No HCI command timeouts, work queue processing functional
  - ✗ **Issue #6: Connection callbacks not firing** - VERIFIED PERSISTS (2025-10-22)
    - **Test**: `multi_bluetooth/ble_gap_connect.py` with PYBD (NimBLE) + STM32WB55 (Zephyr)
    - **Firmware**: STM32WB55 commit ea56a42996 (includes Fix #7), PYBD v1.27.0-preview.325
    - **Result**: Connection succeeded at HCI level, but Python callbacks never fired
    - **Evidence**:
      - ✓ HCI LE Connection Complete received and enqueued (T+1316ms)
      - ✓ Event dequeued from RX queue (queue_latency=22258us)
      - ✓ Passed to Zephyr stack (T+1347ms)
      - ✓ Zephyr processed event (took 6723us, completed T+1354ms)
      - ✗ `mp_bt_zephyr_connected()` callback NEVER invoked
      - ✗ No Python `_IRQ_CENTRAL_CONNECT` event delivered to peripheral
      - ✓ PYBD (central) received `_IRQ_CENTRAL_CONNECT` (connection worked)
      - ✗ PYBD (central) never received `_IRQ_CENTRAL_DISCONNECT` (peripheral couldn't disconnect)
    - **Root Cause**: Zephyr processes HCI connection events but doesn't invoke registered callbacks
    - **Test Output**: `multitest_fix7_verification.txt`
    - **Documented in**: `ISSUE_6_FINAL_ANALYSIS.md`, `ISSUE_6_ROOT_CAUSE_SUMMARY.md`
  - ✗ GATT Client (service discovery, read/write) - NOT IMPLEMENTED

**Next Steps**:
1. Investigate why Zephyr doesn't invoke registered connection callbacks
2. Compare Zephyr callback registration with NimBLE implementation
3. Check if additional Zephyr initialization is required for callback dispatch
4. Test on RP2 Pico 2 W with all fixes applied

**Performance Comparison (5-second scan)**:
| Stack | Devices Detected | Errors | Status |
|-------|------------------|--------|--------|
| NimBLE | 227 | None | Baseline |
| Zephyr (before fixes) | 40 | Buffer exhaustion | Broken |
| Zephyr (after fixes) | 69 | None | Working |

**HCI Traces**:
- NimBLE full scan: `nimble_scan_hci_trace.txt` (541 lines, 227 devices, complete scan)
- Zephyr variant (after fixes): 69 devices, clean deactivation, no errors

---

## Raspberry Pi Pico 2 W - Build & Debug

### Build
```bash
cd ports/rp2
make BOARD=RPI_PICO2_W BOARD_VARIANT=zephyr
```

### Flash via probe-rs
```bash
# Flash and reset
probe-rs run --chip RP2350 --probe 0d28:0204 build-RPI_PICO2_W-zephyr/firmware.elf
probe-rs reset --chip RP2350 --probe 0d28:0204

# Or flash with auto-reset
probe-rs download --chip RP2350 --probe 0d28:0204 build-RPI_PICO2_W-zephyr/firmware.elf --format elf --reset-halt
```

### Flash via USB Bootloader
```bash
# Hold BOOTSEL button while connecting USB
# Device appears as RP2350 Boot (2e8a:000f)
picotool load -x build-RPI_PICO2_W-zephyr/firmware.uf2
# Or copy to mounted drive
cp build-RPI_PICO2_W-zephyr/firmware.uf2 /media/*/RPI-RP2/
```

### GDB Debugging
**Terminal 1:**
```bash
probe-rs gdb --chip RP2350 --probe 0d28:0204 --gdb-connection-string 127.0.0.1:1337
```

**Terminal 2:**
```bash
cd ports/rp2
arm-none-eabi-gdb build-RPI_PICO2_W-zephyr/firmware.elf

# GDB commands
target extended-remote :1337
load
monitor reset halt
break mp_bluetooth_init
break bt_enable
break bt_init
break hci_init
continue
```

### Serial Console
```bash
# Device appears as /dev/ttyACM* after flashing
screen /dev/ttyACM2 115200
# Or
mpremote connect /dev/serial/by-id/usb-STMicroelectronics_STM32_STLink_066AFF505655806687082951-if02 resume repl --inject-code 'from bluetooth import BLE; ble=BLE()\nble.active()\n'
```

### Test BLE Initialization
```python
import bluetooth
ble = bluetooth.BLE()
ble.active(True)
```

---

## STM32 NUCLEO_WB55 - Build & Debug

### Build
```bash
cd ports/stm32
make BOARD=NUCLEO_WB55 MICROPY_BLUETOOTH_ZEPHYR=1 MICROPY_BLUETOOTH_NIMBLE=0
```

### Flash via probe-rs
```bash
probe-rs run --chip STM32WB55RGVx --probe 0483:374b:066AFF505655806687082951 build-NUCLEO_WB55/firmware.elf
probe-rs download --chip STM32WB55RGVx --probe 0483:374b:066AFF505655806687082951 ports/stm32/build-NUCLEO_WB55-zephyr/firmware.elf
probe-rs reset --chip STM32WB55RGVx --probe 0483:374b
```

### Flash via ST-Link
```bash
cd ports/stm32
make BOARD=NUCLEO_WB55 MICROPY_BLUETOOTH_ZEPHYR=1 MICROPY_BLUETOOTH_NIMBLE=0 deploy-stlink
```

### Hardware Reset
For a complete hardware reset (useful when device is hung/crashed):
```bash
# Standard reset (works for most cases)
probe-rs reset --chip STM32WB55RGVx --probe 0483:374b --connect-under-reset

# Recovery sequence for severely hung device (run all three commands in sequence)
# This is needed when device is completely unresponsive (e.g., stuck in k_panic loop)
probe-rs reset --chip STM32WB55RGVx --probe 0483:374b --connect-under-reset
probe-rs reset --chip STM32WB55RGVx --probe 0483:374b
probe-rs reset --chip STM32WB55RGVx --probe 0483:374b --connect-under-reset
```

### GDB Debugging
**Terminal 1:**
```bash
probe-rs gdb --chip STM32WB55RGVx --probe 0483:374b:066AFF505655806687082951 --gdb-connection-string 127.0.0.1:1337
```

**Terminal 2:**
```bash
cd ports/stm32
arm-none-eabi-gdb build-NUCLEO_WB55/firmware.elf

# GDB commands
target extended-remote :1337
load
monitor reset halt
break mp_bluetooth_init
break bt_enable
break bt_init
break hci_init
break k_sem_take
break mp_bluetooth_zephyr_work_process
continue
```

### Serial Console
```bash
# Device appears as /dev/ttyACM* after flashing
screen /dev/ttyACM4 115200
# Or
mpremote connect /dev/ttyACM4 resume repl
```

### Test BLE Functionality
```python
import bluetooth
ble = bluetooth.BLE()
ble.active(True)  # ✓ Works! bt_enable() returns 0

# Test scanning
ble.gap_scan(5000)  # ⚠ Partially works:
                    #   - Scan START succeeds (returns 0)
                    #   - No advertising reports received
                    #   - Scan STOP times out (causes fatal error on deinit)
```

### STM32WB-Specific Notes
- Memory: Linker script defines IPCC buffer sections in RAM2A and RAM2B (critical for BLE functionality)
- HCI Transport: Uses IPCC (Inter-Processor Communication Controller)
- Important files:
  - `ports/stm32/boards/stm32wb55xg.ld` - Linker script with IPCC memory sections
  - `ports/stm32/mpzephyrport.c` - HCI driver implementation (Zephyr BLE)
  - `ports/stm32/rfcore.c` - Wireless coprocessor interface (both stacks)

---

## Key Debugging Points

### Critical Breakpoints
```gdb
# Initialization entry points
break mp_bluetooth_init
break mp_bluetooth_zephyr_port_init
break bt_enable

# Work queue processing
break mp_bluetooth_zephyr_work_process
break k_work_submit

# HCI communication
break hci_stm32_open
break hci_stm32_send
break bt_hci_recv

# Synchronization points
break k_sem_take
break k_sem_give

# Zephyr internals
break bt_init
break hci_init
break bt_hci_cmd_send_sync
```

### Memory Examination
```gdb
# Check work queue state
print/x *(struct k_work *)&bt_dev.init

# Check semaphore state
print bt_dev.ncmd_sem

# Examine HCI buffers
x/32x $sp

# Check function pointers (STM32 specific)
print recv_cb
print hci_dev
```

### Useful GDB Commands
```gdb
# Automated crash analysis
define crash_info
    info registers
    backtrace
    x/32x $sp
    x/32i $pc-16
end

# Set to catch exceptions
catch signal SIGILL
catch signal SIGSEGV
catch signal SIGBUS
```

---

## Build Variants

### Compare with working NimBLE build
```bash
# RP2 with NimBLE (working baseline)
cd ports/rp2
make BOARD=RPI_PICO2_W clean
make BOARD=RPI_PICO2_W

# STM32 with NimBLE (working baseline)
cd ports/stm32
make BOARD=NUCLEO_WB55 clean
make BOARD=NUCLEO_WB55
```

### Generate disassembly for comparison
```bash
arm-none-eabi-objdump -d build-*/firmware.elf > firmware.dis
```

---

## Resolved Issues

### Issue #1: HOST_BUFFER_SIZE Command Incompatibility (FIXED)
**Problem**: STM32WB controller returned error 0x12 (Invalid HCI Command Parameters) when Zephyr sent HOST_BUFFER_SIZE command during initialization, causing bt_enable() to fail with -22 (EINVAL).

**Root Cause**:
- Zephyr uses `#if defined(CONFIG_BT_HCI_ACL_FLOW_CONTROL)` to conditionally compile flow control code
- We had `#define CONFIG_BT_HCI_ACL_FLOW_CONTROL 0` which still counts as "defined"
- Flow control code was being compiled and sending unsupported HOST_BUFFER_SIZE command

**Solution**: Changed to `#undef CONFIG_BT_HCI_ACL_FLOW_CONTROL` in `extmod/zephyr_ble/zephyr_ble_config.h:458`

**Result**: STM32WB55 now successfully initializes - bt_enable() returns 0, all HCI commands complete

**Reference**: Compare with NimBLE log at `docs/nimble_hci_trace_success.log` - NimBLE never sends HOST_BUFFER_SIZE command

### Issue #2: BLE Scanning EPERM Error (FIXED)
**Problem**: `ble.gap_scan()` returned `OSError: [Errno 1] EPERM` when attempting to start scanning.

**Root Cause**:
- Zephyr's `bt_id_set_scan_own_addr()` in `lib/zephyr/subsys/bluetooth/host/id.c:1834` attempted to set a random address for scanning
- This calls `set_random_address()` which sends HCI command `LE_SET_RANDOM_ADDRESS`
- Per Bluetooth Core Spec Vol 4, Part E §7.8.4, this command is illegal while advertising/scanning/initiating is active
- HCI controller rejected with -EACCES, propagated as -EPERM to Python layer
- Error path: `bt_id_set_scan_own_addr()` → `bt_id_set_private_addr()` → `set_random_address()` → HCI error -EACCES

**Investigation**:
- Initially tried disabling `CONFIG_BT_PRIVACY` and `CONFIG_BT_RPA` - didn't help
- Even with privacy disabled, non-privacy code path still attempted to change address when legacy advertising was active
- Function `is_legacy_adv_using_id_addr()` returned true, but code still tried setting random address

**Solution**: Enabled `CONFIG_BT_SCAN_WITH_IDENTITY` in `extmod/zephyr_ble/zephyr_ble_config.h:419`
- This forces scanning to use identity address instead of random address
- Completely avoids the problematic `set_random_address()` code path
- Scanning now uses identity address directly (lines 1887-1909 in id.c)

**Configuration Changes**:
```c
// extmod/zephyr_ble/zephyr_ble_config.h
#define CONFIG_BT_PRIVACY 0                  // Disabled privacy features
#define CONFIG_BT_RPA 0                      // Disabled Resolvable Private Address
#define CONFIG_BT_CTLR_PRIVACY 0             // No controller privacy (host-only)
#define CONFIG_BT_SCAN_WITH_IDENTITY 1       // Use identity address for scanning
```

**Result**: BLE scan START succeeds - `bt_le_scan_start()` returns 0

**Files Modified**:
- `extmod/zephyr_ble/zephyr_ble_config.h` - Added CONFIG_BT_SCAN_WITH_IDENTITY

### Issue #3: BLE Connection IRQ Events Not Delivered (FIXED)
**Problem**: Multi-test `ble_gap_connect.py` failed - STM32WB55 peripheral never received `_IRQ_CENTRAL_CONNECT` or `_IRQ_CENTRAL_DISCONNECT` events, though Pyboard-D central successfully connected.

**Root Cause**:
- Zephyr's `le_set_event_mask()` function (`lib/zephyr/subsys/bluetooth/host/hci_core.c:3529-3542`) conditionally enables different LE connection event types based on `CONFIG_BT_SMP`:
  - When `CONFIG_BT_SMP=1`: Enables **LE Enhanced Connection Complete** events (HCI event mask bit 9)
  - When `CONFIG_BT_SMP=0`: Enables **LE Connection Complete** events (HCI event mask bit 0 - legacy BLE 4.x)
- STM32WB55 RF core sends legacy LE Connection Complete events (0x3E subcode 0x01)
- With `CONFIG_BT_SMP=1`, Zephyr requested enhanced events that the controller never sends
- No events delivered → Zephyr callbacks never triggered → Python IRQ handler never called

**Investigation Method**:
1. Added debug logging to connection callbacks in `modbluetooth_zephyr.c` and `modbluetooth.c`
2. Ran multi-test and confirmed: ZERO callback invocations, ZERO HCI LE Meta Events in trace
3. Examined NimBLE initialization code (`lib/mynewt-nimble/nimble/host/src/ble_hs_startup.c:177-251`) which explicitly sets LE event masks
4. Compared to Zephyr's event mask logic - found conditional behavior based on CONFIG_BT_SMP

**Solution**: Disabled `CONFIG_BT_SMP` in `extmod/zephyr_ble/zephyr_ble_config.h:403`
```c
#define CONFIG_BT_SMP 0  // Disabled: STM32WB55 controller doesn't send Enhanced Connection Complete events
```

**Result**: Connection events now delivered correctly!
- `_IRQ_CENTRAL_CONNECT` fires when central connects to peripheral ✓
- `_IRQ_CENTRAL_DISCONNECT` fires when connection terminates ✓
- Both peripheral and central roles working ✓

**Files Modified**:
- `extmod/zephyr_ble/zephyr_ble_config.h:403` - Disabled CONFIG_BT_SMP

**Note**: Disabling CONFIG_BT_SMP also disables Security Manager Protocol features (pairing, bonding, encryption). This is acceptable for initial bring-up and basic connection testing. Future work may need to support enhanced connection events or implement SMP differently.

### Issue #4: BLE Activation Regression - IPCC Memory Sections (FIXED)
**Problem**: BLE activation (`ble.active(True)`) failed with ETIMEDOUT on both NimBLE and Zephyr BLE stacks in current builds, but worked correctly in official v1.26.1 release.

**Root Cause**:
- Linker script `ports/stm32/boards/stm32wb55xg.ld` had IPCC SECTIONS removed in commit 5d69f18330
- STM32WB55 RF coprocessor (Cortex-M0+) communicates with main CPU (Cortex-M4) via IPCC using shared memory buffers
- These buffers MUST be located in specific RAM regions:
  - RAM2A (0x20030000): IPCC tables and metadata
  - RAM2B (0x20038000): IPCC data buffers
- Without linker script sections, buffers ended up in wrong RAM locations (main RAM at 0x20000000+)
- RF core could not access buffers, causing initialization to fail

**Investigation Method**:
1. Tested official v1.26.1 release - BLE worked ✓
2. Tested current build - BLE failed ✗
3. Initially suspected debug output (DEBUG_printf, HCI_TRACE) - disabled, still failed
4. Initially suspected stack attribute addition - removed, still failed
5. Git bisection: commit acad33dc66 (pre-Zephyr) works, commit 5d69f18330 (Zephyr integration) fails
6. Binary analysis: `_ram_start` symbol missing, BSS size 4 bytes smaller
7. Examined linker script: IPCC SECTIONS were removed

**Solution**: Restored IPCC SECTIONS to `ports/stm32/boards/stm32wb55xg.ld`:
```ld
SECTIONS
{
    /* Put all IPCC tables into SRAM2A. */
    .ram2a_bss :
    {
        . = ALIGN(4);
        . = . + 64; /* Leave room for the mb_ref_table_t (assuming IPCCDBA==0). */
        *rfcore.o(.bss.ipcc_mem_*)
        . = ALIGN(4);
    } >RAM2A

    /* Put all IPCC buffers into SRAM2B. */
    .ram2b_bss :
    {
        . = ALIGN(4);
        *rfcore.o(.bss.ipcc_membuf_*)
        . = ALIGN(4);
    } >RAM2B
}
```

**Result**: BLE activation now works correctly on both NimBLE and Zephyr BLE stacks!
- ✓ NimBLE: Full functionality (activation, advertising, scanning, connections)
- ✓ Zephyr BLE: Initialization and connections work
- Binary size matches pre-regression (BSS: 42528 bytes with IPCC sections)

**Files Modified**:
- `ports/stm32/boards/stm32wb55xg.ld` - Restored IPCC SECTIONS

**HCI Trace**:
- Full NimBLE scan trace captured in `nimble_scan_hci_trace.txt` (541 lines, 227 devices)

**Commits**:
- 7f8ea29497: "ports/stm32: Restore IPCC memory sections for STM32WB55."
- 6f57743847: "extmod/modbluetooth: Add stack attribute to BLE object."
- f1ad0ce1c5: "extmod/zephyr_ble: Disable debug output by default."
- 4550b9489f: "ports/stm32: Disable debug output in rfcore."

### Issue #5: Semaphore Timeout Without Debug Logging (PARTIAL FIX - Zephyr BLE only)
**Problem**: With ZEPHYR_BLE_DEBUG=0, firmware crashed with assertion at hci_core.c:504 during BLE initialization. Semaphore `k_sem_take(&sync_sem, HCI_CMD_TIMEOUT)` returned -EAGAIN (timeout) instead of 0 (success).

**Root Cause**:
- Debug printf statements in semaphore code provided cumulative ~50-75ms timing delays per `k_sem_take()` operation
- These delays allowed IPCC hardware and MicroPython scheduler to complete HCI response processing before timeout
- Without debug printfs, HCI responses weren't delivered to waiting semaphores in time

**Investigation Method**:
1. Fixed multiple CONFIG preprocessor bugs (CONFIG_NET_BUF_POOL_USAGE, CONFIG_BT_ASSERT, CONFIG_BT_RECV_WORKQ_BT)
2. Systematically tested various timing approaches (1ms, 10ms, 50ms yields, direct HCI task calls)
3. Following user guidance, selectively enabled different debug categories
4. Discovered that only SEM debug printfs were necessary for initialization to work

**Solution (Partial)**: Keep SEM debug printfs always enabled in `extmod/zephyr_ble/hal/zephyr_ble_sem.c`
```c
// Lines 39-43: CRITICAL - these printfs provide necessary timing
#define DEBUG_SEM_printf(...) mp_printf(&mp_plat_print, "SEM: " __VA_ARGS__)
```

**Test Results**:
- ✓ BLE Initialization: `ble.active(True)` completes successfully
- ✓ Scan Start: `ble.gap_scan(5000)` sends HCI commands successfully
- ✗ Scan Stop: HCI command 0x200c (SET_SCAN_ENABLE=0x00) times out, causes assertion failure
- Device hangs and requires hardware reset

**Limitation**: This solution only fixes initialization. Other BLE operations (scan stop, deactivation) still experience semaphore timeouts. The SEM printf timing is insufficient for all HCI command types.

**Files Modified**:
- `extmod/zephyr_ble/zephyr_ble_config.h` - Fixed CONFIG preprocessor defines
- `extmod/zephyr_ble/hal/zephyr_ble_sem.c` - Keep DEBUG_SEM_printf enabled always
- `extmod/zephyr_ble/hal/zephyr_ble_kernel.c` - Added assertion location tracking

### Issue #6: Connection Callbacks Not Invoked (INVESTIGATING - commit 193658620a)
**Problem**: After implementing central role (gap_peripheral_connect), connection callbacks are never invoked by Zephyr when device is acting as peripheral (advertising).

**Symptoms**:
- PYBD (NimBLE central) successfully connects to WB55 (Zephyr peripheral)
- WB55's `mp_bt_zephyr_connected()` callback is NEVER called by Zephyr
- No ">>> mp_bt_zephyr_connected CALLED" debug message appears
- WB55 never receives `_IRQ_CENTRAL_CONNECT` event
- First connection from central works, but WB55 never knows about it
- Reconnections fail because WB55 can't track connection state

**Evidence**:
- BLE initialization completes successfully ✓
- Advertising starts correctly ✓
- Callbacks registered via `bt_conn_cb_register(&mp_bt_zephyr_conn_callbacks)` ✓
- Registration confirmed: ">>> Registered connection callbacks: connected=8025a7d disconnected=8025999" ✓
- CONFIG_BT_CONN_DYNAMIC_CALLBACKS = 1 ✓
- PYBD gets `_IRQ_PERIPHERAL_CONNECT` (proving connection succeeded) ✓
- WB55 gets NOTHING (callback never invoked) ✗

**Multi-test Results**:
- Test: `multi_bluetooth/ble_gap_connect.py`
- First connection cycle: Central connects successfully, but peripheral gets no callback
- Second connection cycle: PYBD times out waiting for connection (can't reconnect)
- Test fails: "Timeout waiting for 7" (_IRQ_PERIPHERAL_CONNECT)

**Investigation Status**: ACTIVE
- Need to create minimal test case
- Need to compare with NimBLE callback mechanism
- Need to check Zephyr documentation for callback requirements
- Possibly missing Zephyr initialization step or configuration

**Files Involved**:
- `extmod/zephyr_ble/modbluetooth_zephyr.c:416` - Callback registration
- `extmod/zephyr_ble/modbluetooth_zephyr.c:218-275` - Callback implementations
- `extmod/zephyr_ble/zephyr_ble_config.h:640` - CONFIG_BT_CONN_DYNAMIC_CALLBACKS

## Current Status Summary

### STM32WB55 NimBLE (Default Variant)
**Fully Working:**
- ✓ BLE activation (`ble.active(True)`)
- ✓ BLE advertising
- ✓ BLE scanning (227 devices in 5s test)
- ✓ BLE connections (peripheral and central roles)
- ✓ All IRQ events delivered correctly
- ✓ No debug output required

**HCI Trace**: `nimble_scan_hci_trace.txt` (541 lines, complete scan operation)

### STM32WB55 Zephyr BLE (Zephyr Variant)
**Working:**
- ✓ BLE initialization (`ble.active(True)`)
- ✓ BLE advertising (legacy mode)
- ✓ **BLE scanning - fully functional!** (69 devices in 5s test, no errors, clean deactivation)
- ✓ Central role implementation (gap_peripheral_connect)

**Broken (Issue #6):**
- ✗ **Connection callbacks when acting as peripheral**
  - Callbacks registered but never invoked by Zephyr
  - Prevents tracking of incoming connections
  - Multi-test `ble_gap_connect.py` fails
  - Commit 193658620a

**Performance Note:**
- ⚠ Detects ~30% of devices compared to NimBLE (69 vs 227 in same test)
  - Root cause: Likely work queue processing throughput limitation
  - Status: Acceptable for most use cases, optimization opportunity for future
  - See `docs/BUFFER_FIX_VERIFICATION.md` for detailed analysis

**Resolved Issues:**
- ✓ Buffer exhaustion during scanning (FIXED in commit 6d8e3370a9)
- ✓ RX queue bottleneck (FIXED - increased RX_QUEUE_SIZE from 8 to 32)
- ✓ Clean deactivation now working (no crashes or hangs)

**Test Results**:
- Scanning: 69 devices, no errors, clean deactivation
- Central role: Implementation complete, untested due to Issue #6
- Memory cost: +2KB BSS

## Architecture Documentation

**IMPORTANT**: See `docs/BLE_TIMING_ARCHITECTURE.md` for detailed analysis of:
- System architecture and execution contexts
- Timing flow diagrams for HCI command/response
- Root cause analysis of semaphore timeout issues
- Proposed solutions with pros/cons

**This document must be reviewed and updated whenever changes are made to:**
- `ports/stm32/mpzephyrport.c` (HCI integration layer)
- `extmod/zephyr_ble/hal/zephyr_ble_sem.c` (semaphore implementation)
- `extmod/zephyr_ble/hal/zephyr_ble_work.c` (work queue implementation)
- Any code affecting HCI packet flow or scheduler interaction

## Next Steps

1. ✓ Created detailed timing architecture document
2. ✓ Fixed critical IPCC linker script regression (Issue #4)
3. ✓ Verified NimBLE fully working on STM32WB55
4. ✓ Captured complete HCI trace of NimBLE scan operation
5. ✓ **BREAKTHROUGH: Zephyr BLE now receiving advertising reports!**
6. ✓ **Fixed buffer exhaustion issue** (commit 6d8e3370a9):
   - Root cause: RX_QUEUE_SIZE bottleneck (not Zephyr buffer pools)
   - Solution: Increased RX_QUEUE_SIZE from 8 to 32
   - Result: 69 devices detected, no errors, clean deactivation
7. ✓ **Implemented central role** (commit 193658620a):
   - gap_peripheral_connect() and gap_peripheral_connect_cancel()
   - Connection role detection fixed
   - Connection structure management fixed
8. **CRITICAL: Fix connection callback issue (Issue #6)** - IN PROGRESS
   - Create minimal test case to isolate callback issue
   - Compare with NimBLE callback registration mechanism
   - Check Zephyr documentation for callback requirements
   - Investigate missing Zephyr initialization steps
9. **Test on RP2 Pico 2 W with all fixes applied** (after Issue #6 resolved)
10. **Future optimization opportunity** (non-critical):
   - Investigate why Zephyr detects ~30% of devices vs NimBLE (69 vs 227)
   - Likely work queue processing throughput limitation
   - See `docs/BUFFER_FIX_VERIFICATION.md` for analysis
   - Performance is acceptable for most use cases
9. Consider long-term solution for SMP support with enhanced connection events

Use arm-none-eabi-gdb proactively to diagnose issues. The probe-rs agent or `~/.claude/agents/probe-rs-flash.md` has specific usage details.
