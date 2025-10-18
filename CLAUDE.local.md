# Zephyr BLE Integration for MicroPython

## Goal
Add Zephyr BLE stack as an alternative to NimBLE/BTstack for all MicroPython ports.

## Current Status
- OS abstraction layer implemented (k_work, k_sem, k_mutex, atomic ops)
- Build system integration complete
- **STM32WB55: BLE initialization now working!** bt_enable() completes successfully
- All HCI commands complete (HCI_RESET, READ_LOCAL_FEATURES, READ_SUPPORTED_COMMANDS, LE commands)
- Fix: Disabled CONFIG_BT_HCI_ACL_FLOW_CONTROL (STM32WB controller doesn't support HOST_BUFFER_SIZE command)
- RP2 Pico 2 W: Not yet tested with this fix

## Test Hardware
- **RP2**: Raspberry Pi Pico 2 W (RP2350) with CYW43 BT controller via SPI
- **STM32**: NUCLEO_WB55 (STM32WB55RGVx) with internal BT controller via IPCC

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
mpremote connect /dev/ttyACM2 repl
```

### Test BLE Initialization
```python
import bluetooth
ble = bluetooth.BLE()
ble.active(True)  # Currently hangs here - use GDB to debug
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
probe-rs run --chip STM32WB55RGVx --probe 0483:374b build-NUCLEO_WB55/firmware.elf
```

### Flash via ST-Link
```bash
cd ports/stm32
make BOARD=NUCLEO_WB55 MICROPY_BLUETOOTH_ZEPHYR=1 MICROPY_BLUETOOTH_NIMBLE=0 deploy-stlink
```

### GDB Debugging
**Terminal 1:**
```bash
probe-rs gdb --chip STM32WB55RGVx --probe 0483:374b --gdb-connection-string 127.0.0.1:1337
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
mpremote connect /dev/ttyACM4 repl
```

### Test BLE Initialization
```python
import bluetooth
ble = bluetooth.BLE()
ble.active(True)  # ✓ Now works! bt_enable() returns 0
```

### STM32WB-Specific Notes
- Memory: Linker script reserves 20KB after .bss for Zephyr .noinit section
- HCI Transport: Uses IPCC (Inter-Processor Communication Controller)
- Important files:
  - `ports/stm32/mpzephyrport.c` - HCI driver implementation
  - `ports/stm32/rfcore.c` - Wireless coprocessor interface

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

## Next Steps

1. Complete BLE initialization to full active state (may require additional HCI commands)
2. Test basic BLE operations (advertising, scanning, connections)
3. Test on RP2 Pico 2 W with the flow control fix
4. Verify work queue processing and event handling
5. Remove debug output once stable

Use arm-none-eabi-gdb proactively to diagnose issues. The probe-rs agent or `~/.claude/agents/probe-rs-flash.md` has specific usage details.