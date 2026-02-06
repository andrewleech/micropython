# Zephyr BLE Integration for MicroPython

## Goal
Add Zephyr BLE stack as an alternative to NimBLE/BTstack for all MicroPython ports.

## Milestones

| Tag | Date | Description |
|-----|------|-------------|
| `zephyr-ble-2026-02-05` | 2026-02-05 | Peripheral and central roles working on Pico W and WB55. Remaining limitations are Zephyr architectural (indication type detection, forced notifications). |

---

## Build Variants

### RP2 Pico W (RP2040/RP2350)

Two Zephyr variants are available. **`zephyr_poll` is preferred** — it uses cooperative polling (same architecture as STM32WB55) and avoids FreeRTOS task complexity.

```bash
cd ports/rp2

# Preferred: cooperative polling (no FreeRTOS BLE tasks)
make BOARD=RPI_PICO_W BOARD_VARIANT=zephyr_poll

# Alternative: FreeRTOS-based (async HCI processing via dedicated task)
make BOARD=RPI_PICO_W BOARD_VARIANT=zephyr

# RP2350 (untested — no hardware available)
make BOARD=RPI_PICO2_W BOARD_VARIANT=zephyr
make BOARD=RPI_PICO2_W BOARD_VARIANT=zephyr_poll
```

The `zephyr` variant runs HCI processing on a dedicated FreeRTOS task with 8KB stack. The `zephyr_poll` variant processes HCI from the main task via `mp_sched_schedule_node()`, consistent with the STM32WB55 approach. Both require Python to yield control (via `machine.idle()` or similar) for BLE event processing.

### STM32 NUCLEO_WB55
```bash
cd ports/stm32
make BOARD=NUCLEO_WB55                          # NimBLE (default)
make BOARD=NUCLEO_WB55 BOARD_VARIANT=zephyr     # Zephyr (cooperative polling)
```

### Flash / Serial

```bash
# Pico W: flash via openocd (pyocd unreliable — see MEMORY.md)
openocd -f interface/cmsis-dap.cfg -f target/rp2040.cfg \
  -c "adapter serial 0501083219160908; init; reset halt; \
  program <path>/firmware.elf verify reset; shutdown"

# Pico W: power cycle if hung
~/usb-replug.py 2-3 1

# Serial consoles
mpremote connect /dev/serial/by-id/usb-MicroPython_Board_in_FS_mode_*-if00 repl    # Pico W
mpremote connect /dev/ttyACM* repl                                                  # WB55
```

---

## Current Status

### Passing Tests (11/11 on both Pico W variants)

| Test | Status |
|------|--------|
| `ble_gap_advertise.py` | PASS |
| `ble_gap_connect.py` | PASS |
| `ble_characteristic.py` | PASS |
| `ble_gap_pair.py` | PASS |
| `ble_gap_pair_bond.py` | PASS |
| `ble_subscribe.py` | PASS |
| `ble_irq_calls.py` | PASS |
| `ble_gattc_discover_services.py` | PASS |
| `ble_l2cap.py` | PASS |
| `perf_gatt_notify.py` | PASS |
| `perf_l2cap.py` | PASS |

### Performance (Pico W peripheral + PYBD central)

| Variant | GATT notify | L2CAP throughput |
|---------|------------|-----------------|
| zephyr_poll | ~24ms/notification | ~2184 B/s |
| zephyr (FreeRTOS) | ~33ms/notification | ~2180 B/s |

### Known Limitations

**Indication type detection (Issue #19)**: Zephyr's `att_notify()` and `att_indicate()` both call `bt_gatt_notification()` with identical parameters, losing type info. Indications are misclassified as notifications. Would require upstream Zephyr changes. See `docs/ISSUE_19_PICO_CENTRAL_NOTIFY.md`.

**Forced notifications**: Zephyr only delivers notifications to explicitly subscribed handles. NimBLE delivers all notifications regardless of subscription state. Architectural difference, not a bug.

**Runtime MTU config**: `ble.config(mtu=X)` not supported. MTU is compile-time only via `CONFIG_BT_L2CAP_TX_MTU` (currently 512). `ble_mtu.py` / `ble_mtu_peripheral.py` skipped.

**Thread safety in callbacks**: Callbacks from Zephyr BT context use `mp_bluetooth_is_active()` as a racy guard before accessing root pointers. Low risk in practice — deinit is called from Python context when BLE is idle. Proper fix would be mutex protection or atomic state transitions.

### RP2 Pico 2 W (RP2350)
- Builds successfully, untested (no hardware available)
- HCI init hang previously reported — may be resolved by recent fixes

### STM32WB55
- Same feature set as Pico W (cooperative polling architecture)
- IPCC memory sections in `ports/stm32/boards/stm32wb55xg.ld` (RAM2A/RAM2B) are critical for BLE
- Key files: `ports/stm32/mpzephyrport.c` (HCI driver), `ports/stm32/rfcore.c` (RF coprocessor)

---

## Running BLE Multitests

```bash
# Single test (Pico W as instance0/peripheral, PYBD as instance1/central)
./tests/run-multitests.py \
    -t /dev/serial/by-id/usb-MicroPython_Board_in_FS_mode_e6614c311b7e6f35-if00 \
    -t /dev/serial/by-id/usb-MicroPython_Pyboard_Virtual_Comm_Port_in_FS_Mode_3254335D3037-if01 \
    tests/multi_bluetooth/ble_gap_advertise.py

# All BLE tests
./tests/run-multitests.py \
    -t /dev/serial/by-id/usb-MicroPython_Board_in_FS_mode_e6614c311b7e6f35-if00 \
    -t /dev/serial/by-id/usb-MicroPython_Pyboard_Virtual_Comm_Port_in_FS_Mode_3254335D3037-if01 \
    tests/multi_bluetooth/
```

- **instance0** (first `-t`): peripheral/advertiser — device under test (Zephyr)
- **instance1** (second `-t`): central/scanner — reference device (NimBLE/PYBD)

---

## Test Hardware
- **RP2**: Raspberry Pi Pico W (RP2040) with CYW43 BT controller
- **STM32**: NUCLEO_WB55 (STM32WB55RGVx) with internal BT controller via IPCC
- **PYBD**: PYBD-SF6W with internal BT controller (NimBLE reference)

---

## Resolved Issues

See `docs/RESOLVED_ZEPHYR_BLE_ISSUES.md` for full history of all fixed issues (#6–#22, pairing, L2CAP, perf tests, etc).

---

## Debugging

```gdb
break mp_bluetooth_init
break bt_enable
break mp_bluetooth_zephyr_work_process
break k_sem_take
print bt_dev.ncmd_sem
```

Build with debug output: `make ... ZEPHYR_BLE_DEBUG=1`

---

## Scan Performance (STM32WB55)

5-second scan comparison:
| Stack | Devices detected |
|-------|-----------------|
| NimBLE | 227 |
| Zephyr | 69 (30% detection rate) |
