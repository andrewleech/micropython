# Zephyr BLE Integration for MicroPython

## Goal
Add Zephyr BLE stack as an alternative to NimBLE/BTstack for all MicroPython ports.

---

## Quick Reference

### RP2 Pico W (RP2040/RP2350)
```bash
# Build
cd ports/rp2
make BOARD=RPI_PICO_W BOARD_VARIANT=zephyr      # RP2040
make BOARD=RPI_PICO2_W BOARD_VARIANT=zephyr     # RP2350

# Flash (use skill - handles pyocd automatically)
# Use: flash-pico-w skill

# Power cycle (if device hung)
~/usb-replug.py 2-3 1

# Serial console
mpremote connect /dev/serial/by-id/usb-MicroPython_Board_in_FS_mode_*-if00 repl
```

### STM32 NUCLEO_WB55
```bash
# Build
cd ports/stm32
make BOARD=NUCLEO_WB55                                    # NimBLE (default)
make BOARD=NUCLEO_WB55 BOARD_VARIANT=zephyr              # Zephyr variant

# Flash (use skills)
# Use: flash-nucleo-h5 skill (for WB55)

# Serial console
mpremote connect /dev/ttyACM* repl
```

---

## Current Status

### RP2 Pico W (RP2040) - ✓ Fully Working
- **Zephyr BLE**: All features working, all multitests pass
  - ✓ Scanning (36-48 devices per 1.5s scan)
  - ✓ Advertising
  - ✓ Connections (central and peripheral roles)
  - ✓ GATT server (read/write/notify/indicate)
  - ✓ GATT client (service discovery, read/write)
  - ✓ Soft reset stability (20+ test cycles verified)

### STM32WB55
- **NimBLE**: ✓ Fully working (all features)
- **Zephyr BLE**: Core functionality working (scheduler-based integration)
  - ✓ Scanning (69 devices, ~30% vs NimBLE due to work queue throughput)
  - ✓ Advertising (connectable/non-connectable)
  - ✓ Connections (both roles, stable)
  - ✓ GATT server (read/write/notify/indicate)
  - ✓ GATT client (service discovery, read/write, descriptor operations)
  - ✓ Device name management
  - ✓ 128-bit UUID support
  - ✗ GATTC NOTIFY/INDICATE callbacks (missing `bt_gatt_subscribe()`)
  - ✗ MTU exchange (not implemented)
  - ✗ Pairing/bonding (not implemented)
  - **Multitest Results**: 7/18 pass, 11 fail (missing features), 2 skip (L2CAP)

### RP2 Pico 2 W (RP2350)
- Fix #8: Net_buf crash FIXED (see `docs/NET_BUF_CRASH_FIX.md`)
- Status: HCI init hang - untested recently

---

## Recent Work

### Scheduler Integration (STM32WB55)
**Status**: ✓ Complete - Zephyr BLE working without FreeRTOS

**Implementation**:
- `machine.idle()` calls `mp_handle_pending()` to process scheduler callbacks
- BLE polling scheduled via `mp_sched_schedule_node()`
- Works for all scheduled tasks (BLE, soft timers, etc.)
- Architecture: Cooperative - Python must yield control for BLE processing

**Results**:
- Issue #12 resolved: 5-second GATTC delay eliminated (~14ms now)
- Core BLE operations functional (7/18 multitests passing)
- Missing features: NOTIFY callbacks, MTU exchange, pairing
- Commits: 0c038fb536, 04e3919eaf

### FreeRTOS Integration Plan
**Status**: Documented (see `docs/ZEPHYR_BLE_FREERTOS_PLAN.md`)

Long-term architecture for true asynchronous BLE processing:
- RP2 already using FreeRTOS tasks (fully working)
- Plan for STM32/other ports to adopt FreeRTOS
- Removes dependency on Python yielding control

---

## Test Hardware
- **RP2**: Raspberry Pi Pico W (RP2040), Pico 2 W (RP2350) with CYW43 BT controller
- **STM32**: NUCLEO_WB55 (STM32WB55RGVx) with internal BT controller via IPCC
- **PYBD**: PYBD-SF6W with internal BT controller (NimBLE)

---

## Running BLE Multitests

BLE multitests require two devices communicating over Bluetooth. The test framework runs Python scripts on both devices simultaneously and compares output.

### Finding Serial Ports

Always use `/dev/serial/by-id/` paths to reliably identify devices. These paths are stable across reboots and USB re-enumeration:

```bash
# List all connected serial devices
ls -la /dev/serial/by-id/

# Common patterns:
# Pico W:  usb-MicroPython_Board_in_FS_mode_<serial>-if00
# PYBD:    usb-MicroPython_Pyboard_Virtual_Comm_Port_in_FS_Mode_<serial>-if01
# STLink:  usb-STMicroelectronics_STM32_STLink_<serial>-if02
```

### Running Tests

```bash
# Run a single BLE multitest (Pico W as instance0, PYBD as instance1)
./tests/run-multitests.py \
    -t /dev/serial/by-id/usb-MicroPython_Board_in_FS_mode_e6614c311b7e6f35-if00 \
    -t /dev/serial/by-id/usb-MicroPython_Pyboard_Virtual_Comm_Port_in_FS_Mode_3254335D3037-if01 \
    tests/multi_bluetooth/ble_gap_advertise.py

# Run all BLE multitests
./tests/run-multitests.py \
    -t /dev/serial/by-id/usb-MicroPython_Board_in_FS_mode_e6614c311b7e6f35-if00 \
    -t /dev/serial/by-id/usb-MicroPython_Pyboard_Virtual_Comm_Port_in_FS_Mode_3254335D3037-if01 \
    tests/multi_bluetooth/
```

### Test Instance Roles

- **instance0** (`-t` first): Usually the peripheral/advertiser (device under test)
- **instance1** (`-t` second): Usually the central/scanner (reference device with known-working BLE)

For Zephyr BLE testing, use Pico W (Zephyr) as instance0 and PYBD (NimBLE) as instance1 to validate Zephyr against a known-working stack.

### Key BLE Multitests

| Test | Description |
|------|-------------|
| `ble_gap_advertise.py` | Basic advertising start/stop |
| `ble_gap_connect.py` | Connection establishment and disconnect |
| `ble_characteristic.py` | GATT read/write/notify |

---

## Known Issues

### Missing Features (Zephyr BLE)

**1. GATTC NOTIFY/INDICATE Callbacks**
- **Impact**: Notifications/indications from peripherals not delivered to central
- **Tests affected**: 5 tests (ble_characteristic, ble_subscribe, etc.)
- **Root cause**: `bt_gatt_subscribe()` not implemented in Zephyr integration
- **Priority**: High - common BLE feature

**2. MTU Exchange**
- **Impact**: Cannot negotiate larger MTU for efficient data transfer
- **Tests affected**: 4 tests (ble_mtu, perf_gatt_char_write, etc.)
- **Root cause**: `gattc_exchange_mtu()` returns `EOPNOTSUPP`
- **Priority**: Medium - performance optimization

**3. Pairing/Bonding**
- **Impact**: No secure/encrypted connections
- **Tests affected**: 2 tests (ble_gap_pair, ble_gap_pair_bond)
- **Root cause**: Pairing APIs not implemented
- **Priority**: Medium - security feature

**4. L2CAP**
- **Impact**: No connection-oriented channels
- **Tests affected**: 2 tests (skipped)
- **Root cause**: L2CAP not integrated
- **Priority**: Low - advanced feature

---

## Resolved Issues

### Issue #12: STM32WB55 GATTC 5-Second Delay - FIXED
- 5-second delay between connection and GATT discovery on STM32WB55
- Root cause: `machine.idle()` uses `__WFI()` but doesn't process scheduler
- Python test calls `machine.idle()` in wait loops, scheduled work items never run
- Fixed (2 commits):
  1. Initial: Call `mp_bluetooth_zephyr_hci_uart_wfi()` when BLE active (0c038fb536)
  2. Refactor: Call `mp_handle_pending()` for generic scheduler processing (04e3919eaf)
- Result: Delay reduced from 5+ seconds to ~14ms, GATTC fully functional
- Files: `ports/stm32/modmachine.c`

### Issue #6: Connection Callbacks - FIXED (RP2 Pico W)
- Fixed via GATT client implementation and TX context management
- Commit: 2fe8901cec

### Issue #9: HCI RX Task Hang - FIXED
- HCI RX task caused hangs during shutdown (gap_scan(None) or ble.active(False))
- Fixed: Stop task BEFORE bt_disable(), use task notification for immediate wakeup
- Commit: 82741d16dc

### Issue #10: Soft Reset Hang - FIXED
- Resource leaks caused hang after 4-5 BLE test cycles
- Fixed: static flags reset, work queue reset, GATT memory freed
- Commit: 1cad43a469

### Issue #11: STM32WB55 Spurious Disconnect - FIXED
- Connection callbacks not firing on STM32WB55 Zephyr variant
- Root cause: `mp_bluetooth_hci_poll()` called `mp_bluetooth_zephyr_poll()` but not `run_zephyr_hci_task()`, so HCI packets from IPCC were never processed during the wait loop
- Fixed: `mp_bluetooth_hci_poll()` now calls `run_zephyr_hci_task()` to process HCI events

---

## Build Variants

### STM32 NUCLEO_WB55
```bash
# NimBLE (production-ready)
make BOARD=NUCLEO_WB55

# Zephyr (experimental)
make BOARD=NUCLEO_WB55 BOARD_VARIANT=zephyr
```

### RP2 Pico
```bash
# Standard (NimBLE)
make BOARD=RPI_PICO_W

# Zephyr with FreeRTOS
make BOARD=RPI_PICO_W BOARD_VARIANT=zephyr
```

---

## Debugging

### Common GDB Breakpoints
```gdb
break mp_bluetooth_init
break bt_enable
break mp_bluetooth_zephyr_work_process
break k_sem_take
```

### Memory Examination
```gdb
# Check semaphore state
print bt_dev.ncmd_sem

# Examine HCI buffers
x/32x $sp
```

For detailed debugging guide, see: `docs/DEBUGGING.md`

---

## Documentation

### Architecture & Design
- `docs/BLE_TIMING_ARCHITECTURE.md` - System architecture, timing analysis
- `docs/FREERTOS_INTEGRATION.md` - FreeRTOS integration details

### Fixes & Investigations
- `docs/RESOLVED_ISSUES.md` - All fixed issues (Issues #1-8)
- `docs/NET_BUF_CRASH_FIX.md` - RP2350 net_buf crash analysis
- `docs/BUFFER_FIX_VERIFICATION.md` - Scanning performance analysis

### Test Results
- `docs/PERFORMANCE.md` - Performance comparisons
- `nimble_scan_hci_trace.txt` - Reference HCI trace (NimBLE)

---

## Performance

**5-second scan comparison (STM32WB55)**:
| Stack | Devices | Status |
|-------|---------|--------|
| NimBLE | 227 | Baseline |
| Zephyr | 69 | Working (30% detection rate) |

See `docs/PERFORMANCE.md` for detailed analysis.

---

## Next Steps

1. **RP2 Pico W (RP2040)**: Verify BLE initialization with FreeRTOS
2. **RP2 Pico 2 W (RP2350)**: Fix HCI init hang
3. **GATT client**: Implement NOTIFY/INDICATE callbacks via `bt_gatt_subscribe()` (feature gap)
4. **Performance**: Optimize work queue throughput (non-critical)

---

## STM32WB-Specific Notes

**Critical for BLE functionality**:
- IPCC memory sections in `ports/stm32/boards/stm32wb55xg.ld` (RAM2A/RAM2B)
- See Issue #4 in `docs/RESOLVED_ISSUES.md` for details

**Key files**:
- `ports/stm32/mpzephyrport.c` - HCI driver (Zephyr)
- `ports/stm32/rfcore.c` - RF coprocessor interface
