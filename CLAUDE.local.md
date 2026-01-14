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
- **Zephyr BLE**: Partial - scanning and advertising work
  - ✓ Scanning (69 devices, ~30% vs NimBLE due to work queue throughput)
  - ✓ Advertising
  - ✗ Connections have spurious disconnect (Issue #11)

### RP2 Pico 2 W (RP2350)
- Fix #8: Net_buf crash FIXED (see `docs/NET_BUF_CRASH_FIX.md`)
- Status: HCI init hang - untested recently

---

## Active Work: FreeRTOS Integration

**Objective**: Replace polling-based HAL with FreeRTOS primitives

**Architecture**:
```
FreeRTOS Scheduler
├── Priority MAX-1: HCI RX Task (CYW43 polling)
├── Priority MAX-2: BLE Work Queue Thread
└── Priority 1: Python Main Thread
```

**Status**: Testing RP2040 variant
- Recent fixes: k_current_get(), CYW43 IRQ ordering, NLR thread safety
- See: `docs/FREERTOS_INTEGRATION.md` for details

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

### Issue #11: STM32WB55 Spurious Disconnect (Zephyr BLE)
- Connection callback fires but spurious disconnect follows immediately
- See: `docs/ISSUE_11_STM32WB55_SPURIOUS_DISCONNECT.md`
- Status: Open - STM32WB55 Zephyr variant not production-ready

## Resolved Issues

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
3. **STM32WB55**: Investigate Issue #6 (connection callbacks)
4. **Performance**: Optimize work queue throughput (non-critical)

---

## STM32WB-Specific Notes

**Critical for BLE functionality**:
- IPCC memory sections in `ports/stm32/boards/stm32wb55xg.ld` (RAM2A/RAM2B)
- See Issue #4 in `docs/RESOLVED_ISSUES.md` for details

**Key files**:
- `ports/stm32/mpzephyrport.c` - HCI driver (Zephyr)
- `ports/stm32/rfcore.c` - RF coprocessor interface
