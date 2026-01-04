# BLE Stack Overflow Fix - RP2 Pico W

## Date: 2026-01-04

## Summary

Fixed HardFault crash during `ble.active(False)` on RP2 Pico W by increasing the FreeRTOS main task stack from 8KB to 16KB.

## Root Cause

### Symptoms
- Device crashes during `ble.active(False)` with HardFault
- PC = 0xfffffffe (fault indicator)
- Invalid address in r0/r1 = 0x1040e04 (beyond firmware memory)
- **Smoking gun**: r8-r11 = 0xa5a5a5a5 (FreeRTOS stack fill pattern)
- No output from FreeRTOS stack overflow hook

### Analysis

The crash was a **stack overflow on the main FreeRTOS task** during BLE deinitialization.

Key findings:
1. Both the BLE work thread and HCI RX task are **DISABLED** in the current implementation
   - BLE work thread: Returns immediately at entry (`zephyr_ble_work.c:712`)
   - HCI RX task: Disabled with `#if 0` (`modbluetooth_zephyr.c:567`)
2. All BLE operations run on the **main task** which had only 8KB stack
3. The Zephyr BLE stack has deep call chains during `bt_disable()`:
   ```
   mp_bluetooth_deinit()
     -> bt_disable()
       -> hci_core cleanup
         -> connection teardown
           -> work queue draining
             -> event processing
               -> buffer management
   ```
4. Debug prints via `mp_printf()` add additional stack pressure during deinit
5. Stack overflow corrupted local variables/call frames
6. When trying to print debug messages, stdio_write received corrupted pointer (0x1040e04)
7. HardFault occurred before stack overflow hook could execute
8. Stack overflow hook itself uses `mp_printf()` which can crash if stack is corrupted

### Evidence

**Register state at crash:**
```
r0  = 0x1040e04  <-- Invalid (356KB beyond firmware)
r1  = 0x1040e04  <-- Same corruption
r8  = 0xa5a5a5a5  <-- FreeRTOS stack canary!
r9  = 0xa5a5a5a5
r10 = 0xa5a5a5a5
r11 = 0xa5a5a5a5
```

The 0xa5a5a5a5 pattern is FreeRTOS's stack fill pattern used for overflow detection. These registers contain uninitialized stack values, proving stack overflow corrupted register context.

## Solution

Increased main task stack from 8KB to 16KB in `ports/rp2/main.c:85`:

```c
// Before:
#define FREERTOS_MAIN_TASK_STACK_SIZE (8192 / sizeof(StackType_t))  // 8KB

// After:
#define FREERTOS_MAIN_TASK_STACK_SIZE (16384 / sizeof(StackType_t))  // 16KB
```

## Verification

**Before fix:**
```
Test start
BLE created
BLE activated
[HARDFAULT - no further output]
```

**After fix:**
```
Test start
BLE created
DBG: mp_bluetooth_deinit entry, state=0
DBG: mp_bluetooth_deinit already off/suspended
BLE activated
DBG: mp_bluetooth_deinit entry, state=1
DBG: calling bt_disable()
DBG: bt_disable() returned 0
DBG: calling work_drain()
DBG: work_drain() done
DBG: stopping HCI RX task
DBG: stopping work thread
DBG: threads stopped
DBG: mp_bluetooth_deinit exit
BLE deactivated ✓
```

## Memory Impact

**Stack allocation before:**
- Main task: 8KB
- BLE work thread: 4KB (unused - thread disabled)
- HCI RX task: 4KB (unused - task disabled)
- Service task: 4KB
- FreeRTOS system tasks: ~2KB
- **Total: ~22KB**

**Stack allocation after:**
- Main task: 16KB (+8KB)
- BLE work thread: 8KB (unused - thread disabled)
- HCI RX task: 4KB (unused - task disabled)
- Service task: 4KB
- FreeRTOS system tasks: ~2KB
- **Total: ~34KB** of 264KB RAM (13% - still very safe)

## Files Changed

- `ports/rp2/main.c` - Increased FREERTOS_MAIN_TASK_STACK_SIZE from 8KB to 16KB
- `extmod/zephyr_ble/hal/zephyr_ble_work.c` - Increased BLE_WORK_THREAD_STACK_SIZE from 4KB to 8KB (unused but good practice)

## Related Issues

- **Issue #9**: HCI RX task disabled due to hangs
- **Issue #10**: Soft reset hang (separate issue, not fixed by this change)

## Future Improvements

1. Fix the stack overflow hook to not use `mp_printf()`:
   ```c
   void vApplicationStackOverflowHook(TaskHandle_t xTask, char *pcTaskName) {
       // Use LED blink pattern instead of printf to avoid recursive crash
       __asm volatile ("cpsid i");  // Disable interrupts
       for (;;) {
           gpio_put(PICO_DEFAULT_LED_PIN, 1);
           busy_wait_ms(200);
           gpio_put(PICO_DEFAULT_LED_PIN, 0);
           busy_wait_ms(200);
       }
   }
   ```

2. Consider enabling BLE work thread once GIL issues are resolved to reduce main task load

3. Add stack high water mark monitoring in debug builds:
   ```c
   UBaseType_t uxHighWaterMark = uxTaskGetStackHighWaterMark(NULL);
   mp_printf(&mp_plat_print, "Main stack free: %u bytes\n", uxHighWaterMark * sizeof(StackType_t));
   ```
