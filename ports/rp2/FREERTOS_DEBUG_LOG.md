# RP2 FreeRTOS Integration - Debug Log

## Issue: FreeRTOS Scheduler Failing to Start

**Date**: 2025-12-08
**Status**: CRITICAL - Scheduler returns instead of starting, device hangs at breakpoint

### Symptom

Device firmware builds successfully (345788 bytes text) but hangs during boot:
- OpenOCD confirms device is stuck at `__breakpoint()` instruction in `main.c:216`
- This is the infinite loop after `vTaskStartScheduler()` that should never execute
- REPL connection times out - no serial response

### Root Cause Analysis

The execution path shows:
1. `main()` creates main task with `xTaskCreateStatic()` ✓
2. `main()` calls `vTaskStartScheduler()` ✓
3. **Scheduler returns instead of starting** ✗
4. Device hits `__breakpoint()` in infinite loop at `main.c:216`

#### Why Scheduler Returns

From FreeRTOS `tasks.c:3689-3789`:
```c
void vTaskStartScheduler( void ) {
    xReturn = prvCreateIdleTasks();  // Line 3703

    #if ( configUSE_TIMERS == 1 )
        if( xReturn == pdPASS ) {
            xReturn = xTimerCreateTimerTask();  // Line 3709
        }
    #endif

    if( xReturn == pdPASS ) {
        portDISABLE_INTERRUPTS();
        xSchedulerRunning = pdTRUE;
        ( void ) xPortStartScheduler();  // Line 3765 - should never return
    } else {
        // Idle or timer task creation failed
        configASSERT( xReturn != errCOULD_NOT_ALLOCATE_REQUIRED_MEMORY );
    }
}
```

Since we're hitting `__breakpoint()` (not assertion hang), one of two things happened:
1. **Option A**: `prvCreateIdleTasks()` or `xTimerCreateTimerTask()` failed → took else branch → assertion should fire but doesn't
2. **Option B**: Tasks created successfully → `xPortStartScheduler()` called → `vPortStartFirstTask()` naked assembly returned (impossible)

From RP2040 SMP `port.c:302-346`:
```c
static BaseType_t xPortStartSchedulerOnCore() {
    // Set up interrupts, FIFO handler
    vPortStartFirstTask();  // Line 333 - naked assembly, should NEVER return

    // Should never get here
    vTaskSwitchContext( portGET_CORE_ID() );
    prvTaskExitError();
    return 0;
}
```

The naked `vPortStartFirstTask()` (line 215) uses inline assembly to:
1. Load `pxCurrentTCB[portGET_CORE_ID()]`
2. Load task stack pointer from TCB
3. Restore registers from stack
4. Jump to task entry point with `bx r3`

If this returns, either:
- `pxCurrentTCB` is NULL/invalid
- Stack pointer is corrupted
- Assembly has a bug

### Attempted Fixes

#### Fix 1: TLS NULL Check ✓ (Applied)
**Issue**: Universal backend calling `pvTaskGetThreadLocalStoragePointer()` with NULL handle before scheduler starts.

**Fix**: Added NULL checks in `extmod/freertos/mpthreadport.c:44-59`
```c
struct _mp_state_thread_t *mp_thread_get_state(void) {
    TaskHandle_t task = xTaskGetCurrentTaskHandle();
    if (task == NULL) {
        return NULL;  // Scheduler not started
    }
    return pvTaskGetThreadLocalStoragePointer(task, MP_FREERTOS_TLS_INDEX);
}
```

**Result**: Build successful, but scheduler still fails.

#### Fix 2: PendSV Handler Re-registration ✗ (No Effect)
**Issue**: Code review identified that `configUSE_DYNAMIC_EXCEPTION_HANDLERS (1)` causes FreeRTOS to override MicroPython's `PendSV_Handler` during `vTaskStartScheduler()`.

**Fix**: Added handler re-registration in `main.c:229-235`:
```c
static void rp2_main_loop(void *arg) {
    #if MICROPY_PY_THREAD && PICO_ARM
    // Re-register MicroPython's PendSV handler after FreeRTOS registers its own
    exception_set_exclusive_handler(PENDSV_EXCEPTION, PendSV_Handler);
    #endif

soft_reset:
    mp_thread_init(...);
    mp_init();
    // ... rest of main loop
}
```

**Result**: No effect. The scheduler fails before `rp2_main_loop()` ever executes, so handler never gets registered.

#### Fix 3: Atomic Section Simplification ✓ (Applied)
**Issue**: Code review warned that combining `save_and_disable_interrupts()` with `taskENTER_CRITICAL()` could cause SMP deadlock.

**Fix**: Modified `mpthreadport_rp2.c:40-48` to use only FreeRTOS critical sections:
```c
uint32_t mp_thread_begin_atomic_section(void) {
    taskENTER_CRITICAL();
    return 0;  // FreeRTOS tracks nesting internally on SMP
}

void mp_thread_end_atomic_section(uint32_t state) {
    (void)state;
    taskEXIT_CRITICAL();
}
```

**Result**: Build successful, but scheduler still fails.

**Note**: Original design in `FREERTOS_STATUS.md:55` called for "FreeRTOS critical + IRQ disable combo" for "robust synchronization". This fix removed IRQ disable. May need to restore it.

#### Fix 4: mp_freertos_delay_ms Type Fix ✓ (Applied)
**Issue**: Declaration used `mp_uint_t` which isn't defined early in include chain.

**Fix**: Changed `mpconfigport.h:146` to `unsigned int` (matching STM32).

**Result**: Build successful.

### GDB Investigation Attempts

Multiple GDB debugging sessions attempted but encountered timeouts and connection issues:

1. **Session 1**: Device found at `__breakpoint()` in `main.c:216`
2. **Session 2**: Attempted to breakpoint `vTaskStartScheduler` - timeout
3. **Session 3**: Attempted to breakpoint `xPortStartScheduler` - timeout
4. **Session 4**: OpenOCD connection lost during debugging

**Findings**:
- Device consistently stuck at same location
- Both cores halted at PC 0x000000ea (breakpoint instruction)
- `xSchedulerRunning = 0x0` (scheduler not running)

### Configuration Analysis

#### FreeRTOS Heap Size
- RP2: `configTOTAL_HEAP_SIZE (8192)` - 8KB
- STM32: `configTOTAL_HEAP_SIZE (4096)` - 4KB (single core, no timers)

SMP with timers requires:
- 2 idle tasks: 2 * (200 bytes TCB + 512 bytes stack) = 1424 bytes
- 1 timer task: 200 bytes TCB + 1024 bytes stack = 1224 bytes
- Heap overhead: ~500 bytes
- **Total minimum: ~3148 bytes**

**Verdict**: 8KB should be sufficient.

#### Timer Configuration
- RP2: `configUSE_TIMERS (1)` - Required by RP2040 SMP port for `xEventGroupSetBitsFromISR`
- STM32: `configUSE_TIMERS (0)` - Disabled to save space

**Verdict**: Cannot disable timers on RP2 SMP.

#### Stack Configuration
Main task stack defined in `main.c:85-87`:
```c
#define FREERTOS_MAIN_TASK_STACK_SIZE (8192 / sizeof(StackType_t))
static StaticTask_t main_task_tcb;
static StackType_t main_task_stack[FREERTOS_MAIN_TASK_STACK_SIZE];
```

**Verdict**: Main task uses static allocation (not from heap).

### Comparison with STM32 Port

#### STM32 main.c (Working)
```c
int main(void) {
    // Hardware init
    soft_timer_init();

    #if MICROPY_PY_THREAD
    xTaskCreateStatic(stm32_main, "main", ...);
    vTaskStartScheduler();
    // Never returns
    #else
    stm32_main(NULL);
    #endif
}
```

#### RP2 main.c (Failing)
```c
int main(int argc, char **argv) {
    // Set SEVONPEND
    SCB->SCR |= SCB_SCR_SEVONPEND_Msk;

    pendsv_init();
    soft_timer_init();
    set_sys_clock_khz(SYS_CLK_KHZ, false);

    // ... more hardware init

    #if MICROPY_PY_THREAD
    xTaskCreateStatic(rp2_main_loop, "main", ...);
    vTaskStartScheduler();
    for (;;) { __breakpoint(); }  // ← STUCK HERE
    #else
    rp2_main_loop(NULL);
    #endif
}
```

#### Key Differences

1. **pendsv_init() before scheduler**: RP2 calls `pendsv_init()` before FreeRTOS starts. STM32 doesn't.
2. **SCB->SCR modification**: RP2 sets SEVONPEND bit before scheduler starts.
3. **Clock configuration**: RP2 sets system clock before scheduler.

### Potential Issues

#### Issue 1: pendsv_init() Interference
`pendsv_init()` in `pendsv.c` might be configuring PendSV before FreeRTOS can claim it.

**Evidence**:
- `pendsv.c:204` has naked assembly wrapper for PendSV
- But `configUSE_DYNAMIC_EXCEPTION_HANDLERS (1)` means FreeRTOS also registers PendSV handler
- If `pendsv_init()` sets up static handler, dynamic registration might fail

**Test**: Try moving `pendsv_init()` to AFTER scheduler starts (in `rp2_main_loop()`).

#### Issue 2: pxCurrentTCB Not Initialized
`vPortStartFirstTask()` loads `pxCurrentTCB[portGET_CORE_ID()]` at line 220. If this is NULL, the naked assembly might behave unpredictably.

**Evidence**:
- Main task created with `xTaskCreateStatic()` should set `pxCurrentTCB[0]`
- But if scheduler state is invalid, this might not happen

**Test**: Use GDB to check `pxCurrentTCB` value before `vTaskStartScheduler()` call.

#### Issue 3: Missing configRESET_STACK_POINTER
The SMP `vPortStartFirstTask()` assembly checks `#if configRESET_STACK_POINTER` (line 240).

**Evidence**: This is not defined in `FreeRTOSConfig.h`.

**Test**: Add `#define configRESET_STACK_POINTER (1)` to FreeRTOSConfig.h.

### Next Steps (Priority Order)

#### Priority 1: Compare pendsv_init() Timing
**Action**: Move `pendsv_init()` from `main()` to `rp2_main_loop()` (after scheduler starts)

**Rationale**: STM32 doesn't call `pendsv_init()` before scheduler. This might be interfering with FreeRTOS's dynamic handler registration.

**Implementation**:
```c
int main(void) {
    // DON'T call pendsv_init() here anymore
    soft_timer_init();
    // ... rest of init

    xTaskCreateStatic(rp2_main_loop, "main", ...);
    vTaskStartScheduler();
}

static void rp2_main_loop(void *arg) {
    #if MICROPY_PY_THREAD && PICO_ARM
    // Initialize PendSV after scheduler is running
    pendsv_init();
    exception_set_exclusive_handler(PENDSV_EXCEPTION, PendSV_Handler);
    #endif

soft_reset:
    mp_thread_init(...);
    // ... rest
}
```

#### Priority 2: Add configRESET_STACK_POINTER
**Action**: Add to `FreeRTOSConfig.h`:
```c
#define configRESET_STACK_POINTER (1)
```

**Rationale**: The SMP port's `vPortStartFirstTask()` assembly has conditional code for this. If undefined, behavior might be incorrect.

#### Priority 3: Verify Task Creation
**Action**: Add debug output before scheduler starts to verify tasks created:
```c
#if MICROPY_PY_THREAD
TaskHandle_t main_task = xTaskCreateStatic(...);
if (main_task == NULL) {
    for (;;) { __breakpoint(); }  // Task creation failed
}
vTaskStartScheduler();
```

#### Priority 4: Test Without Timers (If Possible)
**Action**: Temporarily set `configUSE_TIMERS (0)` to see if timer task creation is failing.

**Caveat**: RP2040 SMP port documentation says timers are required. This test might not work.

#### Priority 5: Restore IRQ Disable in Atomic Sections
**Action**: Restore original dual-mechanism atomic sections per `FREERTOS_STATUS.md:55`:
```c
uint32_t mp_thread_begin_atomic_section(void) {
    uint32_t state = save_and_disable_interrupts();
    taskENTER_CRITICAL();
    return state;
}

void mp_thread_end_atomic_section(uint32_t state) {
    taskEXIT_CRITICAL();
    restore_interrupts(state);
}
```

**Rationale**: Original design specified both mechanisms for "robust synchronization" on dual-core SMP.

### Files Modified

- `extmod/freertos/mpthreadport.c` - TLS NULL checks
- `ports/rp2/main.c` - PendSV handler re-registration (ineffective)
- `ports/rp2/mpthreadport_rp2.c` - Atomic section simplification
- `ports/rp2/mpconfigport.h` - mp_freertos_delay_ms type fix

### Build Status

- ✓ Builds successfully: 345788 bytes text, 24876 bytes BSS
- ✗ Scheduler fails to start
- ✗ REPL not responding
- ✗ No serial output

### References

- FreeRTOS SMP Port: `/home/corona/mpy/freertos/lib/FreeRTOS-Kernel/portable/third-party/GCC/RP2040/port.c`
- FreeRTOS Tasks: `/home/corona/mpy/freertos/lib/FreeRTOS-Kernel/tasks.c:3689-3789`
- RP2 Status Doc: `FREERTOS_STATUS.md`
