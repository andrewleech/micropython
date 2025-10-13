# Zephyr BLE STM32WB Hang Diagnostic (2025-10-13)

## Problem Statement
Zephyr BLE stack hangs during `bt_enable()` on STM32WB55 (NUCLEO_WB55 board).

## Investigation Method

### 1. HCI Tracing Setup
Modified `ports/stm32/rfcore.c` to enable HCI packet logging:
- Changed `printf()` to `mp_printf(&mp_plat_print, ...)` in HCI_TRACE blocks
- Traces all HCI commands (>HCI) and responses (<HCI_EVT)

Modified `ports/stm32/mpzephyrport.c` to add send tracing:
- Added `[SEND]` trace in `hci_stm32_send()` to verify Zephyr attempts to send commands

### 2. Firmware Builds Tested

**NimBLE (Working):**
```bash
cd ports/stm32
rm -rf build-NUCLEO_WB55
make BOARD=NUCLEO_WB55 -j8
probe-rs run --chip STM32WB55RGVx build-NUCLEO_WB55/firmware.elf
```

**Zephyr BLE (Hanging):**
```bash
cd ports/stm32
make BOARD=NUCLEO_WB55 BOARD_VARIANT=zephyr -j8
probe-rs run --chip STM32WB55RGVx build-NUCLEO_WB55-zephyr/firmware.elf
```

### 3. Test Commands
```bash
mpremote connect /dev/ttyACM3 exec "import bluetooth; ble = bluetooth.BLE(); ble.active(True); print('Done')"
```

## Key Findings

### NimBLE Success (Baseline)
- **HCI initialization completes in ~300ms**
- First HCI reset command sent at boot
- Full initialization sequence:
  1. Vendor BLE_INIT command
  2. HCI Reset (0x03 0x0c)
  3. Read Buffer Size, Local Version Info
  4. Set Event Mask commands
  5. LE Controller commands (Set Random Address, etc.)
- All commands receive responses from controller
- MAC address retrieval works: `(0, b'\x02\t\x13]0\xd2')`

### Zephyr BLE Failure (Critical)
- **NO `[SEND]` trace messages appear** - Zephyr never calls `hci_stm32_send()`
- **NO HCI trace output** - No commands sent to controller
- Hangs indefinitely in `bt_enable()`
- Firmware boots successfully (REPL works)
- `ble = bluetooth.BLE()` succeeds
- `ble.active(True)` hangs

## Root Cause Analysis

**Conclusion:** Zephyr BLE host stack deadlocks internally **before** attempting any HCI communication.

**Evidence:**
1. HCI transport is functional (proven by NimBLE success)
2. STM32WB wireless coprocessor is functional (proven by NimBLE success)
3. No HCI commands sent (no `[SEND]` messages, no HCI trace output)
4. Hang occurs during Zephyr host initialization, not during HCI transport

**Likely causes:**
1. **Work queue deadlock**: Initialization waiting on work items never submitted
2. **Semaphore ordering**: Code waits on semaphore signaled by un-queued work
3. **Missing kernel init**: Zephyr subsystem not properly initialized
4. **Thread context assumption**: Zephyr code expects threading not available in MicroPython scheduler

## Trace Files

### NimBLE Success Trace
Location: `/tmp/nimble_trace.log`
Capture: `mpremote exec` with HCI_TRACE enabled in rfcore.c

### Zephyr Hang Test
Location: `/tmp/zephyr_hci_trace.log`
Result: Empty (no HCI commands sent)

## Configuration Changes Made

### rfcore.c (lines 58-61, 390-402, 471-477, 642-648)
```c
#define HCI_TRACE (1)

// Changed printf to mp_printf for UART output
mp_printf(&mp_plat_print, "[% 8d] <%s(%02x", mp_hal_ticks_ms(), info, buf[0]);
```

### mpzephyrport.c (line 324)
```c
// Added send trace
mp_printf(&mp_plat_print, "[SEND] HCI type=0x%02x len=%d\n", h4_type, total_len);
```

### mpzephyrport.c (line 58)
```c
// Enabled hard fault debug
pyb_hard_fault_debug = 1;
```

## Next Investigation Steps

1. Use GDB to break in `bt_enable()` and step through initialization
2. Add debug output to Zephyr work queue and semaphore functions
3. Check if Zephyr expects threading context not available in cooperative scheduler
4. Compare Zephyr initialization requirements vs NimBLE's cooperative model
5. Review Zephyr's `CONFIG_BT_*` requirements for non-threaded environments

## Hardware Setup
- Board: NUCLEO-WB55 (STM32WB55RGV6)
- Debug Probe: ST-Link V2-1 (0483:374b)
- Serial: /dev/ttyACM3 @ 115200 baud
- Transport: IPCC (Inter-Processor Communication Controller to wireless coprocessor)

## Update: Boot Crash Investigation (2025-10-14)

### Issue: Firmware wouldn't boot via probe-rs
After the HCI trace investigation, discovered the firmware was crashing during
early boot (Reset_Handler) before reaching main().

### Root Cause: Vector Table in RAM
The NUCLEO_WB55 board config sets `MICROPY_HW_ENABLE_ISR_UART_FLASH_FUNCS_IN_RAM=1`
which uses `boards/common_isr_ram/common_isr.ld` placing the vector table at
RAM address 0x20000000 instead of flash address 0x08000000.

**Why this fails:**
- ARM Cortex-M4 reads vector table from **flash at 0x08000000** during reset
- RAM variant requires debugger preload (works with OpenOCD, fails with probe-rs)
- probe-rs directly resets CPU which reads garbage from flash, causing immediate crash

**GDB Investigation:**
- Used auto-stepping GDB script to trace execution
- Found firmware stuck in infinite loop in Reset_Handler data copy (0x080004de-0x080004e8)
- Stack pointer corruption observed (0x2002eff8 → 0x8d5acff6 garbage)
- readelf showed .isr_vector at 0x20000000 (RAM) not 0x08000000 (flash)
- 21 LOAD segments vs 7 in working NimBLE build (later confirmed as correct - .noinit sections)

**Fix Applied:**
```mk
# ports/stm32/boards/NUCLEO_WB55/mpconfigvariant_zephyr.mk
# Use ROM vector table for probe-rs compatibility
MICROPY_HW_ENABLE_ISR_UART_FLASH_FUNCS_IN_RAM = 0
```

**Result:**
- ✅ Firmware boots successfully via probe-rs
- ✅ Reaches REPL without errors
- ✅ Python execution works correctly
- ✅ HCI transport functional (verified with trace output)
- ❌ Still hangs in bt_enable() (same deadlock issue)

### Current Status (2025-10-14)
Both RP2 Pico 2 W and STM32WB55 now boot successfully but exhibit identical hang
behavior during Zephyr BLE initialization:

**Working:**
- MicroPython boots to REPL
- `bluetooth.BLE()` object creation succeeds
- HCI transport layer functional
- Controller communication working (HCI commands execute successfully)

**Failing:**
- `ble.active(True)` hangs indefinitely
- Hang occurs in `bt_enable()` → `bt_init()`
- Internal Zephyr host deadlock before any HCI commands sent
- Eventually causes watchdog reset or hardfault (crash loop)

**Latest HCI Trace Output (showing controller communication works):**
```
[ 1259359] <VEND_EVT(12:ff:03:00:92:00)
[ 1259363] >HCI(:10:66:fc:24:00:00:00:00:00:00:00:00:00:00:00:00:00:00:01:01:00:79:00:00:00:00:00:00:ff:ff:ff:ff:48:01:01:01:00:00:00:00)
[ 1259378] <VEND_RESP(11:0e:04:01:66:fc:00)
[ 1259383] >HCI(:01:03:0c:00)
[ 1259387] <HCI_EVT(04:0e:04:01:03:0c:00)
[ 1259391] >HCI(:10:73:fc:01:00)
[ 1259395] <VEND_RESP(11:0e:04:01:73:fc:00)
[bt_enable] bt_hci_open OK, calling bt_monitor_send
[bt_enable] No callback, calling bt_init
[HANG OCCURS HERE - never returns from bt_init()]
```

The controller responds correctly to all commands, but the Zephyr host initialization
never completes.
