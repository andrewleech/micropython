# Zephyr BLE Integration for MicroPython

## Goal
Add Zephyr BLE stack as an alternative to NimBLE/BTstack for all MicroPython ports.

## Current Status
- OS abstraction layer implemented (k_work, k_sem, k_mutex, atomic ops)
- Build system integration complete
- **STM32WB55: BLE initialization works, scanning partially working**
  - bt_enable() completes successfully
  - BLE scan START succeeds (no EPERM error)
  - **ISSUE**: No advertising reports received during scan
  - **ISSUE**: Scan STOP command times out (10s), causes fatal error
- All HCI initialization commands complete (HCI_RESET, READ_LOCAL_FEATURES, READ_SUPPORTED_COMMANDS, LE commands)
- Fix #1: Disabled CONFIG_BT_HCI_ACL_FLOW_CONTROL (STM32WB controller doesn't support HOST_BUFFER_SIZE command)
- Fix #2: Enabled CONFIG_BT_SCAN_WITH_IDENTITY to fix scanning EPERM error
- RP2 Pico 2 W: Not yet tested with these fixes

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
mpremote connect /dev/ttyACM2 resume repl
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
probe-rs run --chip STM32WB55RGVx --probe 0483:374b:066AFF505655806687082951 build-NUCLEO_WB55/firmware.elf
```

### Flash via ST-Link
```bash
cd ports/stm32
make BOARD=NUCLEO_WB55 MICROPY_BLUETOOTH_ZEPHYR=1 MICROPY_BLUETOOTH_NIMBLE=0 deploy-stlink
```

### Hardware Reset
For a complete hardware reset (useful when device is hung/crashed):
```bash
# Explicit reset with connect-under-reset (most reliable)
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

**Remaining Issues**:
- No advertising reports received during scan (no devices detected)
- Scan STOP command (LE_SET_SCAN_ENABLE disable) times out waiting for HCI response
  - Command sent: `>HCI_CMD(01:0c:20:02:00:00)`
  - No response received from controller
  - Causes 10s timeout in `bt_hci_cmd_send_sync()` → fatal error
- Likely issue: HCI event routing or STM32WB RF core not delivering events properly

## Next Steps

1. **URGENT**: Investigate why scan STOP command times out
   - Check HCI event processing in work queue
   - Verify STM32WB RF core event delivery
   - May need to check IPCC interrupt handling
2. Debug why no advertising reports are received
   - Verify HCI event routing for LE_ADVERTISING_REPORT events
   - Check if events are making it from RF core to host stack
3. Once scanning works, test advertising functionality
4. Test BLE connections (central and peripheral roles)
5. Test on RP2 Pico 2 W with both fixes applied

Use arm-none-eabi-gdb proactively to diagnose issues. The probe-rs agent or `~/.claude/agents/probe-rs-flash.md` has specific usage details.