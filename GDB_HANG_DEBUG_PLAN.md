# GDB Debugging Plan for Soft Reset Hang

## Problem
After using BLE, `machine.soft_reset()` hangs during SystemExit exception handling, before reaching the `soft_reset_exit` cleanup code in main.c.

## GDB Debug Approach (Better than Debug Prints)

Once firmware is successfully flashed, use GDB to attach and see exact hang location:

### 1. Start GDB Server
```bash
pyocd gdbserver --probe 0501083219160908 --target rp2040
```

### 2. Connect GDB
```bash
arm-none-eabi-gdb ./ports/rp2/build-RPI_PICO_W-zephyr/firmware.elf
(gdb) target remote localhost:3333
```

### 3. Run Test Until Hang
```bash
# In another terminal, run the test
mpremote connect /dev/serial/by-id/usb-MicroPython_Board_in_FS_mode_e6614c311b7e6f35-if00 \
  exec "$(cat test_soft_reset_hang.py)"

# When it hangs, break in GDB:
(gdb) Ctrl+C
```

### 4. Examine Hang Location
```gdb
# Show current location and call stack
(gdb) bt
(gdb) info threads

# Examine what the code is waiting on
(gdb) print *some_semaphore
(gdb) print *some_mutex

# Check scheduler state
(gdb) print MP_STATE_VM(sched_state)
(gdb) print MP_STATE_VM(sched_head)
(gdb) print MP_STATE_THREAD(mp_pending_exception)
```

### 5. Common Hang Scenarios to Check

**Scenario A: Hung in Scheduler Callback**
- Backtrace shows `mp_sched_run_pending()` → `callback()` → blocking call
- Check what the callback is doing (likely BLE-related)

**Scenario B: Hung in FreeRTOS Primitive**
- Backtrace shows `xSemaphoreTake()`, `xQueueReceive()`, etc.
- Check which semaphore/mutex and who owns it
- Likely: stale task handle in recursive mutex (like cyw43_smp_mutex issue)

**Scenario C: Hung in GC Finalization**
- Backtrace shows `gc_sweep_all()` or finalizer execution
- A BLE object's `__del__` method is blocking

**Scenario D: Hung in mp_handle_pending**
- Backtrace shows infinite loop in exception/callback processing
- Scheduler state machine stuck

## Expected Outcome

GDB backtrace will immediately show:
1. Exact function and line number where execution is stuck
2. Full call stack showing how we got there
3. Variable values showing what's being waited on
4. Thread state if hung in threading primitive

This is much faster than debug prints and provides complete context.

## Current Blocker

Cannot flash firmware due to pyocd/picotool issues:
- pyocd load hangs indefinitely
- Device not enumerating after erase (no firmware to boot)
- picotool requires BOOTSEL mode (needs physical button press or working firmware)

Need to resolve flash access before GDB debugging can proceed.
