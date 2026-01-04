# Soft Reset Hang - GDB Investigation Findings

## Date: 2026-01-04

## Summary

Investigation into soft reset hang after BLE usage on RP2 Pico W using GDB revealed the actual hang location is different from initial assumptions.

## Test Script
```python
import bluetooth
import machine

print("Test start")
ble = bluetooth.BLE()
print("BLE created")
ble.active(True)
print("BLE activated")
ble.active(False)  # <-- HANGS HERE
print("BLE deactivated")
print("Calling soft_reset...")
machine.soft_reset()
```

## Findings

### 1. Debug Print HardFault

**Attempt**: Added `mp_printf()` debug prints to `mp_sched_run_pending()` and `mp_handle_pending_internal()`

**Result**: Immediate HardFault on boot
- PC: 0xfffffffe (fault indicator)
- LR: 0x100342c5 (`mp_hal_stdout_tx_strn+32`)
- Device in CPU lockup state

**Root Cause**: `mp_printf()` cannot be safely called from:
- Atomic sections (MICROPY_BEGIN_ATOMIC_SECTION/END)
- Before USB CDC initialization
- Interrupt contexts

**Lesson**: Debug instrumentation using prints is not viable for low-level scheduler/atomic code.

###2. Actual Hang Location

**Previous Assumption**: Hang occurs during `machine.soft_reset()` → SystemExit exception handling

**Actual Location**: Hang occurs during `ble.active(False)` → `mp_bluetooth_deinit()`

**Evidence**:
```
Test start
BLE created
DBG: mp_bluetooth_deinit entry, state=0
DBG: mp_bluetooth_deinit already off/suspended
BLE activated
[HANG - no further output]
```

The debug prints from commit 109658ea3f show:
1. First `mp_bluetooth_deinit()` call completes (state=0, already off)
2. `ble.active(True)` succeeds
3. **Hang occurs after "BLE activated" is printed**
4. Never reaches "BLE deactivated" (which would print after `ble.active(False)`)

### 3. Device State When Hung

Using GDB to examine hung device:
- CPU in HardFault/lockup state
- PC = 0xfffffffe
- Cannot unwind stack ("ARM M in lockup state, stack unwinding terminated")
- Device appears hung but is actually crashed

## Revised Understanding

The hang is **NOT** in soft reset exception handling. It's in BLE deactivation:

1. `ble.active(True)` succeeds
2. `ble.active(False)` is called
3. Somewhere during BLE stack shutdown, a fault occurs
4. Device enters lockup state
5. mpremote times out waiting for response

## Next Steps

1. ~~Investigate SystemExit handling~~ (Incorrect assumption)
2. **Investigate `mp_bluetooth_deinit()` for RP2 Pico W**
   - What happens during `bt_disable()`?
   - Work queue draining - could this deadlock?
   - Thread/task cleanup - could this fault?
3. **Use GDB with breakpoints** instead of prints:
   - Set breakpoint in `mp_bluetooth_deinit()`
   - Step through BLE shutdown
   - Catch fault as it happens
4. **Check FreeRTOS task state** during deinit:
   - Work thread deletion while work pending?
   - HCI RX task issues?
   - Mutex/semaphore deadlock?

## GDB Commands for Next Investigation

```bash
# Start GDB server
pyocd gdbserver --probe 0501083219160908 --target rp2040 &

# Connect GDB
arm-none-eabi-gdb build-RPI_PICO_W-zephyr/firmware.elf
(gdb) target remote localhost:3333
(gdb) break mp_bluetooth_deinit
(gdb) break bt_disable
(gdb) continue

# Run test via mpremote in another terminal
# When breakpoint hits, step through with 'next' and 'step'
# Watch for fault or hang
```

## Conclusion

The soft reset hang is actually a **BLE deactivation fault**, not an exception handling issue. The device crashes (HardFault) during `ble.active(False)`, likely in FreeRTOS task cleanup or Zephyr BLE stack shutdown.
