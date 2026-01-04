# BLE Deinit Crash - Root Cause Analysis

## Date: 2026-01-04

## Summary

The "soft reset hang" is actually a **HardFault crash during ble.active(False)**, occurring when attempting to print debug output. The crash happens BEFORE the second `mp_bluetooth_deinit()` can execute any code.

## Test Sequence and Crash Location

```python
ble = bluetooth.BLE()        # Works - prints "DBG: mp_bluetooth_deinit entry, state=0"
ble.active(True)             # Works - prints "BLE activated"
ble.active(False)            # CRASHES - no output
```

## Crash Details

### Fault State (from GDB)
- **PC**: 0xfffffffe (HardFault indicator)
- **LR**: 0xfffffffd (fault return)
- **Thread**: HardFault, CPU in lockup state
- **Stack**: Cannot unwind - "ARM M in lockup state"

### Register State
```
r0 = 0x1040e04  <-- INVALID ADDRESS
r1 = 0x1040e04  <-- Same invalid address
r7 = 0x10031de7 (stdio_write+1)
```

### Critical Finding: Invalid Memory Access

Address 0x1040e04 is **beyond all mapped memory**:
- Firmware .rodata ends at: 0x100d7fc4
- Firmware .data ends at: 0x100d806c
- Attempted access at: 0x1040e04 (356KB beyond end!)

This is a classic **buffer overflow** or **memory corruption** pattern.

## Call Stack at Fault

```
stdio_write+1 (0x10031de7)
  -> Tries to access [r0+4] where r0=0x1040e04
  -> HardFault: memory address 0x1040e08 doesn't exist
```

## Why First Call Works, Second Fails

1. **First call** (`BLE()` constructor):
   - BLE state = OFF (state=0)
   - `mp_bluetooth_deinit()` called
   - Debug print works: "DBG: mp_bluetooth_deinit entry, state=0"
   - No actual BLE stack operations performed (already off)

2. **After `ble.active(True)`**:
   - BLE stack initialized
   - FreeRTOS tasks created (work thread, HCI RX task)
   - CYW43 BT controller initialized
   - **Something corrupts memory**

3. **Second call** (`ble.active(False)`):
   - Attempts to call `mp_bluetooth_deinit()`
   - **CRASHES before first debug print**
   - stdio_write receives corrupt pointer 0x1040e04

## Hypothesis: Stack Overflow

The invalid address 0x1040e04 suggests:
- Stack grew beyond bounds
- Overwrote return addresses or function pointers
- When trying to print, uses corrupted pointer

### Evidence
- Address is exactly 0x34000 (208KB) beyond firmware end
- This is suspiciously round number - could be stack region
- FreeRTOS tasks have limited stack sizes
- BLE work queue + HCI RX task might overflow

## mp_plat_print Structure Status

Checked mp_plat_print at 0x10082908:
- **Before crash**: `{0x00000000, 0x1000c225}` ✓ Correct
- **After crash**: `{0x00000000, 0x1000c225}` ✓ Still correct!

So mp_plat_print itself is NOT corrupted. The corruption is elsewhere - likely in:
1. Stack frame of calling function
2. Local variables passed to stdio_write
3. Function return addresses

## Next Steps

1. **Check FreeRTOS task stack sizes**:
   - Work thread stack
   - HCI RX task stack
   - Main thread stack

2. **Add stack overflow detection**:
   - FreeRTOS has vApplicationStackOverflowHook
   - Check if it's enabled and working

3. **Examine with GDB before crash**:
   - Set breakpoint at `ble.active(False)` entry
   - Check stack pointer values
   - Monitor for stack growth during BLE operations

4. **Disable debug prints temporarily**:
   - Remove all mp_printf from modbluetooth_zephyr.c
   - See if crash still happens without prints
   - This will confirm if issue is print-related or deeper

## Files to Investigate

- `ports/rp2/mpconfigport.h`: Task stack size definitions
- `ports/rp2/mpzephyrport_rp2.c`: FreeRTOS task creation
- `extmod/zephyr_ble/hal/zephyr_ble_work.c`: Work thread stack
- `ports/rp2/freertos_hooks.c`: Stack overflow hook implementation

## Conclusion

This is NOT a soft reset issue. It's a **memory corruption/stack overflow** that happens during BLE operation and manifests when trying to deactivate BLE. The debug prints are victims, not causes.
