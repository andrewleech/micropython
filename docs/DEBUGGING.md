# Debugging Guide - Zephyr BLE Integration

## GDB Debugging

### Critical Breakpoints

Use these breakpoints to debug BLE initialization and operation:

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

## RP2 Pico W Debugging

### Setup

**Terminal 1 - GDB Server:**
```bash
probe-rs gdb --chip RP2350 --probe 0d28:0204 --gdb-connection-string 127.0.0.1:1337
```

**Terminal 2 - GDB Client:**
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

### Common Issues

**Vector Table Corruption:**
- Symptom: All-zero vector table after flash
- Cause: probe-rs didn't actually write to flash
- Solution: Power cycle with `~/usb-replug.py 2-3 1`, use USB bootloader

**NLR Thread Safety:**
- Symptom: Hard fault at `nlr_jump()` in work thread
- Cause: Debug output in work thread calls `mp_printf()` → NLR
- Solution: Disable debug output in work threads (NLR not thread-safe)

## STM32 NUCLEO_WB55 Debugging

### Setup

**Terminal 1 - GDB Server:**
```bash
probe-rs gdb --chip STM32WB55RGVx --probe 0483:374b:066AFF505655806687082951 --gdb-connection-string 127.0.0.1:1337
```

**Terminal 2 - GDB Client:**
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

### Hardware Reset

For hung/crashed devices:
```bash
# Standard reset
probe-rs reset --chip STM32WB55RGVx --probe 0483:374b --connect-under-reset

# Recovery sequence (severely hung)
probe-rs reset --chip STM32WB55RGVx --probe 0483:374b --connect-under-reset
probe-rs reset --chip STM32WB55RGVx --probe 0483:374b
probe-rs reset --chip STM32WB55RGVx --probe 0483:374b --connect-under-reset
```

### IPCC Memory Verification

Critical for STM32WB55 BLE functionality:

```gdb
# Check IPCC buffer placement
info symbol ipcc_mem_dev_evt_pool
info symbol ipcc_membuf_sys_cmd_rsp_evt

# Should be in RAM2A (0x20030000) and RAM2B (0x20038000)
# If in main RAM (0x20000000), linker script IPCC SECTIONS missing
```

## Serial Console Access

### RP2 Pico W
```bash
# Power cycle if hung
~/usb-replug.py 2-3 1

# Serial console
mpremote connect /dev/serial/by-id/usb-MicroPython_Board_in_FS_mode_*-if00 repl
```

### STM32 NUCLEO_WB55
```bash
# Serial console
mpremote connect /dev/ttyACM* repl
```

## Test Scripts

### Basic BLE Test
```python
import bluetooth
ble = bluetooth.BLE()
ble.active(True)
print("BLE active:", ble.active())
```

### Scanning Test
```python
import bluetooth
import time

def scan_callback(event, data):
    if event == 5:  # _IRQ_SCAN_RESULT
        addr_type, addr, adv_type, rssi, adv_data = data
        print(f"Device: {bytes(addr).hex()} RSSI: {rssi}")

ble = bluetooth.BLE()
ble.active(True)
ble.irq(scan_callback)
ble.gap_scan(5000)
time.sleep(6)
print("Scan complete")
```

### Connection Test (Peripheral)
```python
import bluetooth
import time

def irq_callback(event, data):
    if event == 1:  # _IRQ_CENTRAL_CONNECT
        print("Central connected:", data)
    elif event == 2:  # _IRQ_CENTRAL_DISCONNECT
        print("Central disconnected:", data)

ble = bluetooth.BLE()
ble.active(True)
ble.irq(irq_callback)

# Start advertising
ble.gap_advertise(100, b'\x02\x01\x06')
print("Advertising...")

# Wait for connections
while True:
    time.sleep(1)
```

## Common Debug Output

### Successful BLE Init (STM32WB55)
```
BLE_NEW: enter, bluetooth=<address>
mp_bluetooth_init: enter
mp_bluetooth_zephyr_port_init called
bt_enable() returned: 0
mp_bluetooth_init: exit, return 0
BLE active: True
```

### Successful Scan Start
```
Scan START: bt_le_scan_start() returned 0
```

### Connection Event (Expected)
```
>>> mp_bt_zephyr_connected CALLED
IRQ: event=1 (CENTRAL_CONNECT), conn_handle=1
```

## Performance Profiling

### Scan Performance Comparison
```bash
# Run 5-second scan, count unique devices
python3 test_scan.py

# Compare results:
# NimBLE baseline: 227 devices
# Zephyr current: 69 devices (~30%)
```

### HCI Trace Capture
```bash
# Enable HCI tracing in zephyr_ble_config.h
#define CONFIG_BT_DEBUG_HCI_CORE 1

# Redirect output to file
mpremote connect /dev/ttyACM* exec "import test_scan" > hci_trace.txt
```

## Troubleshooting

### Issue: Semaphore Timeouts
**Symptom**: `k_sem_take()` returns -EAGAIN, bt_enable() fails
**Cause**: Work queue not processing HCI responses in time
**Debug**:
```gdb
break k_sem_take
commands
  silent
  printf "k_sem_take: count=%d\\n", sem->count
  continue
end
```

### Issue: Callbacks Not Firing
**Symptom**: Python IRQ handler never called for connections
**Cause**: Zephyr not invoking registered callbacks
**Debug**:
```gdb
break mp_bt_zephyr_connected
break mp_bt_zephyr_disconnected

# If breakpoints never hit, Zephyr not calling callbacks
```

### Issue: Buffer Exhaustion
**Symptom**: Few advertising reports received, RX queue full messages
**Cause**: Work queue processing slower than HCI event rate
**Debug**: Check `docs/BUFFER_FIX_VERIFICATION.md` for analysis

## Additional Resources

- **Architecture**: `docs/BLE_TIMING_ARCHITECTURE.md`
- **Resolved Issues**: `docs/RESOLVED_ISSUES.md`
- **FreeRTOS Integration**: `docs/FREERTOS_INTEGRATION.md`
- **Performance Analysis**: `docs/PERFORMANCE.md`
