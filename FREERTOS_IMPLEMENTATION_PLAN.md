# MicroPython FreeRTOS Threading Backend: Implementation Plan

**Document Version:** 1.1
**Date:** 2025-12-07
**Status:** In Progress (QEMU complete, STM32 in progress)
**Companion Document:** FREERTOS_THREADING_REQUIREMENTS.md

---

## Overview

This document provides a phased implementation plan for the universal FreeRTOS threading backend. Each task is flagged with the recommended agent type:

- **[HAIKU]** - Simple, well-defined tasks suitable for cheap-code-writer agent
- **[REGULAR]** - Complex tasks requiring deeper analysis and careful implementation
- **[REVIEW]** - Code review checkpoints using principal-code-reviewer agent

---

## Phase 1: Directory Structure and Scaffolding

**Goal:** Create the `extmod/freertos/` directory with all file stubs and basic structure.

### 1.1 Create Directory Structure [HAIKU]

```
extmod/freertos/
├── mpthreadport.h
├── mpthreadport.c
├── mp_freertos_service.h
├── mp_freertos_service.c
├── mp_freertos_hal.h
├── mp_freertos_hal.c
├── freertos.mk
├── freertos.cmake
├── FreeRTOSConfig_template.h
└── freertos_hooks_template.c
```

**Task:** Create empty files with standard MicroPython copyright headers and include guards.

**Acceptance:** All files exist with proper headers, compile (as empty stubs).

### 1.2 Write FreeRTOSConfig_template.h [HAIKU]

**Task:** Create a documented template FreeRTOSConfig.h with all required macros from Section 5 of requirements. Include comments explaining each setting.

**Input:** Requirements Section 5.1 (Mandatory Configuration), Section 5.2 (Recommended Configuration)

**Output:** Well-commented template that ports can copy and customize.

### 1.3 Define Core Data Structures [HAIKU]

**File:** `mpthreadport.h`

**Task:** Define the following structures per Section 6 of requirements:
- `mp_thread_state_t` enum
- `mp_thread_t` structure
- `mp_thread_mutex_t` structure
- `mp_thread_recursive_mutex_t` structure

**Input:** Requirements Section 6.1, 6.2

**Acceptance:** Header compiles, structures match spec exactly.

### 1.4 Write freertos_hooks_template.c [HAIKU]

**File:** `extmod/freertos/freertos_hooks_template.c`

**Task:** Create template for FreeRTOS callback hooks required by all ports:
```c
// Static allocation callbacks for Idle and Timer tasks
void vApplicationGetIdleTaskMemory(...);
void vApplicationGetTimerTaskMemory(...);

// Stack overflow hook (for debugging)
void vApplicationStackOverflowHook(TaskHandle_t xTask, char *pcTaskName);

// Tick hook (optional, can call mp_handle_pending)
void vApplicationTickHook(void);
```

**Input:** Requirements Section 5.3, FreeRTOS documentation

**Output:** Well-commented template that ports can copy and customize.

**Acceptance:** Template compiles, includes all required callbacks.

---

## Phase 2: Build System Integration

**Goal:** Create build system fragments that ports can include.

### 2.1 Write freertos.mk [HAIKU]

**File:** `extmod/freertos/freertos.mk`

**Task:** Create Makefile fragment per Section 9.1 of requirements:
- Define `FREERTOS_SRC_C` with kernel sources
- Define `FREERTOS_INC` include paths
- Add `extmod/freertos/mpthreadport.c` to sources
- Document required port additions (architecture-specific port.c)

**Input:** Requirements Section 9.1

**Acceptance:** Can be included by STM32 Makefile without errors (sources don't need to compile yet).

### 2.2 Write freertos.cmake [HAIKU]

**File:** `extmod/freertos/freertos.cmake`

**Task:** Create CMake fragment per Section 9.2 of requirements:
- Define `micropython_freertos` INTERFACE library
- Add kernel sources and include directories
- Document required port additions

**Input:** Requirements Section 9.2

**Acceptance:** Can be included by RP2 CMakeLists.txt without errors.

### 2.3 [REVIEW] Build System Review

Review build system fragments for:
- Correct conditional compilation guards (`MICROPY_PY_THREAD`)
- Proper path handling (relative vs absolute)
- Missing dependencies

---

## Phase 3: HAL Integration Layer

**Goal:** Implement FreeRTOS-aware delay and critical section functions.

### 3.1 Implement mp_freertos_hal.h [HAIKU]

**File:** `extmod/freertos/mp_freertos_hal.h`

**Task:** Define HAL function declarations:
```c
void mp_freertos_delay_ms(mp_uint_t ms);
void mp_freertos_delay_us(mp_uint_t us);
mp_uint_t mp_freertos_ticks_ms(void);
mp_uint_t mp_freertos_ticks_us(void);
```

**Acceptance:** Header compiles with FreeRTOS includes.

### 3.2 Implement mp_freertos_hal.c [REGULAR]

**File:** `extmod/freertos/mp_freertos_hal.c`

**Task:** Implement HAL functions:
- `mp_freertos_delay_ms()` using `vTaskDelay()` with GIL release consideration
- `mp_freertos_delay_us()` using busy-wait or hardware timer
- Tick functions using `xTaskGetTickCount()`

**Complexity:** Moderate - must handle edge cases (0 delay, pre-scheduler operation)

**Input:** Requirements Section 8.1, 8.2

**Acceptance:** Functions work correctly before and after scheduler starts.

### 3.3 Implement Time Functions [HAIKU]

**File:** Port-specific `mphalport.c` or `mp_freertos_hal.c`

**Task:** Implement time functions that work with FreeRTOS:
- `mp_hal_time_ns()` must use FreeRTOS ticks, not direct hardware timers
- Example: `return (uint64_t)mp_hal_ticks_us() * 1000ULL;`
- Ensure consistency with `mp_hal_ticks_ms()` and `mp_hal_ticks_us()`

**Critical:** Ports must not use hardware timers directly for `time.time()` when threading is enabled, as FreeRTOS manages the system tick.

**Input:** QEMU implementation in `ports/qemu/mphalport.c:104`

**Acceptance:** `time.time()` and `time.time_ns()` return consistent, monotonic values.

---

## Phase 4: Thread State Management

**Goal:** Implement thread-local state access via FreeRTOS TLS.

### 4.1 Implement Thread State Functions [HAIKU]

**File:** `mpthreadport.c`

**Task:** Implement `mp_thread_get_state()` and `mp_thread_set_state()`:
```c
mp_state_thread_t *mp_thread_get_state(void) {
    return pvTaskGetThreadLocalStoragePointer(NULL, MP_FREERTOS_TLS_INDEX);
}

void mp_thread_set_state(mp_state_thread_t *state) {
    vTaskSetThreadLocalStoragePointer(NULL, MP_FREERTOS_TLS_INDEX, state);
}
```

**Input:** Requirements Section 2.4.7

**Acceptance:** Functions compile and link.

### 4.2 Implement mp_thread_get_id() [HAIKU]

**File:** `mpthreadport.c`

**Task:** Return current task handle as mp_uint_t:
```c
mp_uint_t mp_thread_get_id(void) {
    return (mp_uint_t)xTaskGetCurrentTaskHandle();
}
```

**Acceptance:** Returns unique value per thread.

---

## Phase 5: Mutex Implementation

**Goal:** Implement mutex primitives using FreeRTOS semaphores.

### 5.1 Implement Basic Mutex Functions [REGULAR]

**File:** `mpthreadport.c`

**Task:** Implement mutex API using FreeRTOS binary semaphores:
- `mp_thread_mutex_init()` - create binary semaphore with `xSemaphoreCreateBinaryStatic()`
- `mp_thread_mutex_lock()` - `xSemaphoreTake()` with timeout handling
- `mp_thread_mutex_unlock()` - `xSemaphoreGive()`

**Complexity:** Must handle:
- Wait parameter (blocking vs non-blocking)
- Pre-scheduler operation (return success if scheduler not started)
- Static allocation (GC-first strategy)

**Input:** Requirements Section 6.2

**Acceptance:** Basic lock/unlock works, non-blocking try-lock works.

### 5.2 Implement Recursive Mutex Functions [HAIKU]

**File:** `mpthreadport.c`

**Task:** Implement recursive mutex API:
- `mp_thread_recursive_mutex_init()` - `xSemaphoreCreateRecursiveMutexStatic()`
- `mp_thread_recursive_mutex_lock()` - `xSemaphoreTakeRecursive()`
- `mp_thread_recursive_mutex_unlock()` - `xSemaphoreGiveRecursive()`

**Input:** Pattern from 5.1, use recursive variants

**Acceptance:** Same thread can acquire lock multiple times.

### 5.3 [REVIEW] Mutex Implementation Review

Review for:
- Deadlock potential
- Correct static allocation
- Pre-scheduler safety
- Priority inversion handling (FreeRTOS provides by default with mutexes)

---

## Phase 6: Thread Lifecycle - Core Implementation

**Goal:** Implement thread creation, initialization, and cleanup.

### 6.1 Implement Global Thread List [HAIKU]

**File:** `mpthreadport.c`

**Task:** Define global state:
```c
static mp_thread_mutex_t thread_mutex;
static mp_thread_t *thread_list_head;
static mp_thread_t main_thread;

MP_REGISTER_ROOT_POINTER(struct _mp_thread_t *mp_thread_list_head);
```

**Acceptance:** Compiles, root pointer registered.

### 6.2 Implement mp_thread_init() [REGULAR]

**File:** `mpthreadport.c`

**Task:** Initialize threading subsystem and adopt main thread:
1. Initialize `thread_mutex`
2. Setup `main_thread` structure:
   - `id = xTaskGetCurrentTaskHandle()`
   - `state = MP_THREAD_STATE_RUNNING`
   - `stack = NULL` (special case - main stack not GC-allocated)
   - `tcb = NULL` (not static-allocated)
3. Add to thread list
4. Set TLS pointer for main thread

**Complexity:** Must be called at correct point in startup sequence (after GC init, scheduler may or may not be running).

**Input:** Requirements Section 4.2 (from plan_freertos_backend.md)

**Acceptance:** Main thread registered, `mp_thread_get_state()` works from main.

### 6.3 Implement Thread Entry Wrapper [REGULAR]

**File:** `mpthreadport.c`

**Task:** Implement `freertos_entry_wrapper()` per Section 4.3 of requirements:
1. Initialize thread-local `mp_state_thread_t` on stack
2. Set TLS pointer
3. Call `mp_thread_start()`
4. Execute user entry function
5. On return, lock mutex and set state to FINISHED
6. Enter eternal `vTaskDelay(portMAX_DELAY)` loop

**Complexity:** Critical path - must handle exceptions, ensure cleanup happens.

**Input:** Requirements Section 4.3

**Acceptance:** Threads can start and finish without crashes.

### 6.4 Implement mp_thread_create() [REGULAR]

**File:** `mpthreadport.c`

**Task:** Implement thread creation per Section 4.2-4.3:
1. Call reaper to clean finished threads
2. Validate and align stack size
3. Allocate TCB via `m_new(StaticTask_t, 1)`
4. Allocate stack via `m_new(StackType_t, stack_len)`
5. Allocate thread struct via `m_new_obj(mp_thread_t)`
6. Initialize thread struct fields
7. Call `xTaskCreateStatic()` with wrapper function
8. Handle allocation failures (cleanup partial allocations)
9. Add to thread list
10. Return thread ID

**Complexity:** High - memory management, error handling, thread safety.

**Input:** Requirements Section 4

**Acceptance:** Can create multiple threads, proper cleanup on failure.

### 6.5 Implement Reaper Mechanism [REGULAR]

**File:** `mpthreadport.c`

**Task:** Implement `mp_thread_reap_dead_threads()` per Section 4.2:
1. Lock thread mutex
2. Iterate thread list
3. For FINISHED threads:
   - Unlink from list
   - Free stack via `m_del(StackType_t, ...)`
   - Free TCB via `m_del(StaticTask_t, ...)`
   - Free thread struct via `m_del_obj(mp_thread_t, ...)`
4. Unlock mutex

**Complexity:** Moderate - linked list manipulation under lock, safe iteration while removing.

**Input:** Requirements Section 4.2

**Acceptance:** Memory doesn't leak over repeated thread creation/completion.

### 6.6 Implement mp_thread_start() and mp_thread_finish() [HAIKU]

**File:** `mpthreadport.c`

**Task:** Implement lifecycle hooks:
```c
void mp_thread_start(void) {
    mp_thread_mutex_lock(&thread_mutex, 1);
    // Thread is already in list from mp_thread_create
    mp_thread_mutex_unlock(&thread_mutex);
}

void mp_thread_finish(void) {
    // State set to FINISHED in entry wrapper
    // Actual cleanup done by reaper
}
```

**Acceptance:** Called correctly by entry wrapper.

### 6.7 [REVIEW] Thread Lifecycle Review

Critical review of:
- Memory leak potential
- Race conditions in list manipulation
- Exception safety
- Stack overflow potential in entry wrapper
- Correct state transitions

---

## Phase 7: GC Integration

**Goal:** Ensure GC can scan all thread stacks for root pointers.

### 7.1 Implement mp_thread_gc_others() [REGULAR]

**File:** `mpthreadport.c`

**Task:** Implement GC root scanning per Section 7.1:
1. Lock thread mutex
2. Get current task handle
3. For each thread in list:
   - Scan thread struct itself (`gc_collect_root(&th, 1)`)
   - Scan arg pointer (`gc_collect_root(&th->arg, 1)`)
   - Skip current thread (being traced normally)
   - Skip non-RUNNING threads
   - Scan entire stack buffer (`gc_collect_root(th->stack, th->stack_len)`)
4. Unlock mutex

**Complexity:** High - critical for correctness, must not miss any roots.

**Input:** Requirements Section 7.1

**Acceptance:** Objects on thread stacks survive GC. Run `stress_freertos_gc.py`.

### 7.2 [REVIEW] GC Integration Review

Critical review - GC bugs cause silent corruption:
- All thread stacks scanned?
- Main thread handled correctly?
- Mutex contention with GC stop-the-world?
- No roots missed?

---

## Phase 8: GIL Integration

**Goal:** Implement Global Interpreter Lock using mutex primitives and ensure responsive thread scheduling.

### 8.1 Implement GIL Exit with Yield [HAIKU]

**File:** `mpthreadport.c` or `mp_freertos_hal.c`

**Task:** Override GIL exit to include yield:
```c
void mp_thread_gil_exit(void) {
    mp_thread_mutex_unlock(&MP_STATE_VM(gil_mutex));
    taskYIELD();  // Allow other threads to acquire GIL
}
```

**Note:** Requires macro definition in header:
```c
#define MP_THREAD_GIL_EXIT() mp_thread_gil_exit()
```

**Input:** Requirements Section 8.3

**Acceptance:** Multiple Python threads make progress (no GIL starvation).

### 8.2 Configure Event Poll Hook [HAIKU]

**File:** Port-specific `mpconfigport.h`

**Task:** Each port must define `MICROPY_EVENT_POLL_HOOK` to integrate with FreeRTOS scheduler:
```c
#if MICROPY_PY_THREAD
#define MICROPY_EVENT_POLL_HOOK \
    do { \
        extern void mp_handle_pending(bool); \
        mp_handle_pending(true); \
        MP_THREAD_GIL_EXIT(); \
        taskYIELD(); \
        MP_THREAD_GIL_ENTER(); \
    } while (0);

#define MICROPY_THREAD_YIELD() taskYIELD()
#else
#define MICROPY_EVENT_POLL_HOOK \
    do { \
        extern void mp_handle_pending(bool); \
        mp_handle_pending(true); \
    } while (0);

#define MICROPY_THREAD_YIELD()
#endif
```

**Critical:** This hook is called frequently by the bytecode interpreter and enables:
- Handling of pending exceptions and keyboard interrupts
- Fair scheduling between Python threads via GIL release
- Cooperative multitasking within Python code

**Input:** QEMU implementation in `ports/qemu/mpconfigport.h:99-117`

**Acceptance:** REPL remains responsive, Ctrl+C works, threads yield properly.

---

## Phase 9: Service Task Framework (Optional)

**Goal:** Implement background service task management for USB, network, BLE.

many of these tasks currently rely of the C scheduled task system, using pendsv as a low priority interrupt to trigger the tasks at the appropriate time. this is also used for soft timers. investigate this entire system to identify if the pendsv aspects of this can be wrapped/replaced by a high priority thread as part of this phase planning.

### 9.1 Define Service Task API [HAIKU]

**File:** `mp_freertos_service.h`

**Task:** Define service task structures and API per Section 13.8.3-13.8.4:
- `mp_freertos_service_t` structure
- Service flag definitions
- Priority level macros
- Function declarations

**Input:** Requirements Section 13.8

**Acceptance:** Header compiles, all types defined.

### 9.2 Implement Service Registration [REGULAR]

**File:** `mp_freertos_service.c`

**Task:** Implement service lifecycle:
- `mp_freertos_service_init()` - initialize service array
- `mp_freertos_service_register()` - add to registry
- `mp_freertos_service_start()` - create task with static allocation
- `mp_freertos_service_stop()` - signal task to stop, delete
- `mp_freertos_service_stop_all()` - stop non-essential services
- `mp_freertos_service_deinit()` - cleanup on soft reset

**Complexity:** Moderate - task lifecycle management, coordination.

**Input:** Requirements Section 13.8.5

**Acceptance:** Services can be registered, started, stopped.

### 9.3 [REVIEW] Service Framework Review

Review for:
- Soft reset handling
- Essential service protection
- Race conditions in start/stop
- Memory management

---

## Port Integration Checklist

**This checklist applies to all port integrations (Phases 10-12).**

Each threading-enabled port must implement:

- [ ] **FreeRTOSConfig.h** - Port-specific FreeRTOS configuration with correct CPU clock
- [ ] **freertos_hooks.c** - Copy and customize from template (static allocation callbacks, stack overflow hook)
- [ ] **mpconfigport.h threading section** with:
  - `MICROPY_PY_THREAD_GIL (1)`
  - `MICROPY_STACK_CHECK_MARGIN (1024)` - **Critical for stress_recurse.py**
  - `MICROPY_MPTHREADPORT_H "extmod/freertos/mpthreadport.h"`
  - `MICROPY_EVENT_POLL_HOOK` with GIL release and `taskYIELD()` - **Critical for responsiveness**
  - `MICROPY_THREAD_YIELD() taskYIELD()`
  - `mp_hal_delay_ms` macro redirection to `mp_freertos_delay_ms`
- [ ] **mphalport.c modifications**:
  - Conditionally compile non-FreeRTOS `mp_hal_delay_ms()` with `#if !MICROPY_PY_THREAD`
  - Ensure time functions (`mp_hal_time_ns()`) use FreeRTOS ticks when threading enabled
- [ ] **Build system integration** - Include `freertos.mk` or `freertos.cmake` conditionally
- [ ] **main.c modifications** - Start FreeRTOS scheduler, call `mp_thread_init()`
- [ ] **Testing**:
  - Threaded build compiles and runs
  - Non-threaded build still works (regression check)
  - All thread tests pass: `run-tests.py thread/*.py`

---

## Phase 10: STM32 Port Integration

**Goal:** Integrate FreeRTOS backend with STM32 port.

### 10.1 Create STM32 FreeRTOSConfig.h [HAIKU]

**File:** `ports/stm32/FreeRTOSConfig.h`

**Task:** Create port-specific config:
- Copy from template
- Set `configCPU_CLOCK_HZ` for STM32F4/F7 (use runtime detection or board-specific)
- Configure memory settings for typical STM32 RAM
- Enable stack overflow checking for debug builds

**Input:** Template + Requirements Section 5

**Acceptance:** Compiles for PYBV11 board.

### 10.2 Update STM32 Makefile [REGULAR]

**File:** `ports/stm32/Makefile`

**Task:** Add conditional FreeRTOS integration:
```makefile
ifeq ($(MICROPY_PY_THREAD),1)
FREERTOS_DIR = $(TOP)/lib/FreeRTOS-Kernel
include $(TOP)/extmod/freertos/freertos.mk

# Architecture-specific port
ifeq ($(MCU_SERIES),f4)
SRC_C += $(FREERTOS_DIR)/portable/GCC/ARM_CM4F/port.c
CFLAGS_MOD += -I$(FREERTOS_DIR)/portable/GCC/ARM_CM4F
endif
# ... similar for f7, h7
endif
```

**Complexity:** Moderate - must handle multiple MCU series, not break non-threaded builds.

**Input:** Requirements Section 10.1

**Acceptance:** `make BOARD=PYBV11 MICROPY_PY_THREAD=1` compiles.

### 10.3 Update STM32 mpconfigport.h [HAIKU]

**File:** `ports/stm32/mpconfigport.h`

**Task:** Add complete threading configuration per Port Integration Checklist:
```c
// Threading configuration
#if MICROPY_PY_THREAD
#define MICROPY_PY_THREAD_GIL (1)
#define MICROPY_STACK_CHECK_MARGIN (1024)
#define MICROPY_MPTHREADPORT_H "extmod/freertos/mpthreadport.h"
// FreeRTOS-aware delay (declared here to avoid include order issues)
void mp_freertos_delay_ms(unsigned int ms);
#define mp_hal_delay_ms mp_freertos_delay_ms
#endif

// Event poll hook - must release/acquire GIL for threading
#if MICROPY_PY_THREAD
#include "FreeRTOS.h"
#include "task.h"
#define MICROPY_EVENT_POLL_HOOK \
    do { \
        extern void mp_handle_pending(bool); \
        mp_handle_pending(true); \
        MP_THREAD_GIL_EXIT(); \
        taskYIELD(); \
        MP_THREAD_GIL_ENTER(); \
    } while (0);

#define MICROPY_THREAD_YIELD() taskYIELD()
#else
#define MICROPY_EVENT_POLL_HOOK \
    do { \
        extern void mp_handle_pending(bool); \
        mp_handle_pending(true); \
    } while (0);

#define MICROPY_THREAD_YIELD()
#endif
```

**Critical:** `MICROPY_STACK_CHECK_MARGIN` is required for `stress_recurse.py` to pass. Without it, deep recursion causes hard stack overflow instead of raising `RecursionError`.

**Input:** Requirements Section 2.4.5, QEMU implementation

**Acceptance:** Compiles with and without threading, all thread tests pass.

### 10.4 Modify STM32 main.c Startup [REGULAR]

**File:** `ports/stm32/main.c`

**Task:** Modify startup to work with FreeRTOS:

**Option A (Recommended):** Create main task and start scheduler:
```c
#if MICROPY_PY_THREAD
int main(void) {
    // Minimal hardware init
    HAL_Init();
    SystemClock_Config();

    // Create main task
    xTaskCreate(mp_main_task, "MP", MP_MAIN_STACK_SIZE / sizeof(StackType_t),
                NULL, MP_MAIN_PRIORITY, NULL);

    // Start scheduler (never returns)
    vTaskStartScheduler();
}

static void mp_main_task(void *arg) {
    // Full init and REPL loop
    mp_thread_init();
    // ... existing main() body ...
}
#else
// Existing non-threaded main()
#endif
```

**Complexity:** High - must preserve existing behavior, handle soft reset correctly.

**Input:** Requirements Section 10.1, step 5

**Acceptance:** REPL works, soft reset works, threading works.

### 10.5 Remove/Disable pybthread.c [REGULAR]

**File:** `ports/stm32/Makefile`, `ports/stm32/pybthread.c`

**Task:** Conditionally exclude old threading code:
```makefile
ifneq ($(MICROPY_PY_THREAD_FREERTOS),1)
SRC_C += pybthread.c
endif
```

**Complexity:** Must ensure no regressions if old backend is kept as option.

**Acceptance:** No duplicate symbol errors, old backend still available if needed.

### 10.6 Create freertos_hooks.c [HAIKU]

**File:** `ports/stm32/freertos_hooks.c` (new file)

**Task:** Copy `extmod/freertos/freertos_hooks_template.c` and customize for STM32:
- Adjust idle task stack size if needed based on STM32 RAM
- Implement `vApplicationStackOverflowHook()` to print error via UART (optional, useful for debugging)
- Keep timer task callback if `configUSE_TIMERS` is enabled

**Note:** This file provides the static allocation callbacks required by FreeRTOS when `configSUPPORT_STATIC_ALLOCATION` is enabled. All ports need this file.

**Input:** Template from Phase 1.4, Requirements Section 5.3

**Acceptance:** File compiles, scheduler starts without assertion failures.

### 10.7 [REVIEW] STM32 Integration Review

Full integration review:
- Build with and without threading
- Soft reset behavior
- Memory usage comparison
- All thread tests pass

---

## Phase 11: RP2 Port Integration

**Goal:** Integrate FreeRTOS backend with RP2 port, replacing Pico SDK multicore.

### 11.1 Create RP2 FreeRTOSConfig.h [HAIKU]

**File:** `ports/rp2/FreeRTOSConfig.h`

**Task:** Create RP2-specific config:
- Configure for 125MHz RP2040
- Enable SMP (`configNUMBER_OF_CORES = 2`)
- Configure for Pico SDK integration

**Acceptance:** Compiles for RPI_PICO board.

### 11.2 Update RP2 CMakeLists.txt [REGULAR]

**File:** `ports/rp2/CMakeLists.txt`

**Task:** Add conditional FreeRTOS integration:
- Include `freertos.cmake`
- Add FreeRTOS-Kernel-SMP as submodule or use Pico SDK's bundled version
- Link architecture-specific port

**Complexity:** CMake integration with Pico SDK is complex.

**Acceptance:** `cmake -DMICROPY_PY_THREAD=1` configures successfully.

### 11.3 Update RP2 mpconfigport.h [HAIKU]

**File:** `ports/rp2/mpconfigport.h`

**Task:** Add threading configuration similar to STM32.

### 11.4 Modify RP2 main.c for Service Tasks [REGULAR]

**File:** `ports/rp2/main.c`

**Task:** Implement service task architecture per Section 13.4:
- Create init task at high priority
- Spawn USB, network, MicroPython tasks
- Delete init task after spawning

**Complexity:** High - significant restructuring of startup.

**Input:** Requirements Section 13.5

**Acceptance:** USB and networking remain responsive during Python execution.

### 11.5 Implement RP2 Service Registration [REGULAR]

**File:** `ports/rp2/mpserviceport.c`

**Task:** Register port-specific services:
- TinyUSB device task
- lwIP task (if networking enabled)
- CYW43 task (for Pico W)

**Input:** Requirements Section 13.8.4

**Acceptance:** Services start automatically, handle soft reset.

### 11.6 [REVIEW] RP2 Integration Review

Review for:
- Dual-core safety
- USB responsiveness during Python load
- WiFi/BLE functionality (Pico W)
- Memory usage

---

## Phase 12: Secondary Port Integration

**Goal:** Enable threading on mimxrt, SAMD51, nRF52840.

### 12.1 mimxrt Integration [REGULAR]

**Files:** `ports/mimxrt/Makefile`, `ports/mimxrt/mpconfigport.h`, `ports/mimxrt/FreeRTOSConfig.h`

**Task:** Follow STM32 pattern:
- Create FreeRTOSConfig.h for Cortex-M7
- Update Makefile with conditional inclusion
- Update mpconfigport.h

**Complexity:** Moderate - clean slate, no existing threading to migrate.

**Acceptance:** `make BOARD=MIMXRT1060_EVK MICROPY_PY_THREAD=1` compiles and runs.

### 12.2 SAMD51 Integration [REGULAR]

**Files:** `ports/samd/Makefile`, `ports/samd/mpconfigport.h`, `ports/samd/FreeRTOSConfig.h`

**Task:** Follow STM32 pattern with RAM-conscious settings.

**Acceptance:** `make BOARD=SEEED_XIAO MICROPY_PY_THREAD=1` (SAMD51 board) compiles.

### 12.3 nRF52840 Integration [REGULAR]

**Files:** `ports/nrf/Makefile`, `ports/nrf/mpconfigport.h`, `ports/nrf/FreeRTOSConfig.h`

**Task:** Follow STM32 pattern with SoftDevice coexistence considerations.

**Complexity:** Moderate - must coordinate with BLE SoftDevice interrupt priorities.

**Acceptance:** `make BOARD=PCA10059 MICROPY_PY_THREAD=1` compiles.

---

## Phase 13: Testing and Verification

**Goal:** Verify implementation correctness across all ports.

### 13.1 Run Existing Thread Tests [HAIKU]

**Task:** Execute `tests/thread/` suite on each integrated port:
```bash
cd tests
./run-tests.py --target pyboard thread/
./run-tests.py --target rp2 thread/
```

**Acceptance:** All tests pass.

### 13.2 Create stress_freertos_gc.py [HAIKU]

**File:** `tests/thread/stress_freertos_gc.py`

**Task:** Copy stress test from Requirements Section 11.3.

**Acceptance:** Test file exists, can be executed.

### 13.3 Run Stress Tests [REGULAR]

**Task:** Execute stress tests on hardware:
- Run for extended periods (1+ hours)
- Monitor memory usage
- Check for crashes, hangs, corruption

**Acceptance:** No crashes, memory stable, counter accurate.

### 13.4 Verify Non-Threaded Builds [HAIKU]

**Task:** For each integrated port, verify that non-threaded builds still work:
```bash
# Clean build without threading
make BOARD=<board> clean
make BOARD=<board>  # Without MICROPY_PY_THREAD

# Verify it builds and runs basic tests
./run-tests.py --target <target> basics/
```

**Critical:** This regression check ensures threading integration didn't break existing non-threaded functionality.

**Acceptance:** Non-threaded builds compile, run, and pass basic tests on all integrated ports.

### 13.5 QEMU CI Integration [REGULAR]

**Task:** Configure QEMU ARM build with threading for CI:
- Create QEMU board definition with threading enabled (MPS2_AN385_THREAD)
- Add to CI pipeline
- Test with both `exec:` and `execpty:` modes:
  - `exec:` mode: Direct stdio, faster but less realistic
  - `execpty:` mode: PTY-based serial, more realistic for interactive tests
- Tune timeouts for emulation speed (stress tests may be slower)

**Note:** QEMU testing provides fast feedback without hardware. All 32 thread tests should pass (1 skip: disable_irq.py).

**Input:** QEMU implementation in `ports/qemu/boards/MPS2_AN385_THREAD/`

**Acceptance:** Thread tests run in CI without hardware, all tests pass.

### 13.6 [REVIEW] Final Implementation Review

Comprehensive review:
- Code style (run codeformat.py)
- Memory safety
- Thread safety
- Documentation completeness
- Test coverage

---

## Phase 14: Documentation and Cleanup

**Goal:** Complete documentation and prepare for merge.

### 14.1 Update Port READMEs [HAIKU]

**Task:** Add threading documentation to:
- `ports/stm32/README.md`
- `ports/rp2/README.md`
- Other integrated ports

### 14.2 Create extmod/freertos/README.md [HAIKU]

**Task:** Document the FreeRTOS backend:
- Overview and purpose
- Integration steps (summary)
- Configuration reference
- Troubleshooting guide

### 14.3 Update CODECONVENTIONS.md [HAIKU]

**Task:** Add any new conventions for FreeRTOS code if needed.

### 14.4 Final Code Cleanup [REGULAR]

**Task:**
- Remove debug code
- Ensure all TODOs addressed
- Run codeformat.py
- Run codespell

**Acceptance:** `pre-commit run --all-files` passes.

---

## Dependency Graph

```
Phase 1 (Scaffolding)
    │
    ├──► Phase 2 (Build System)
    │
    ├──► Phase 3 (HAL) ──────────────────────────────────────┐
    │                                                         │
    └──► Phase 4 (Thread State) ──► Phase 5 (Mutex) ──► Phase 6 (Lifecycle)
                                                              │
                                                              ▼
                                              Phase 7 (GC) ──► Phase 8 (GIL)
                                                              │
                                    ┌─────────────────────────┴─────────────────────────┐
                                    │                                                   │
                                    ▼                                                   ▼
                          Phase 9 (Service Framework)                         Phase 10 (STM32)
                                    │                                                   │
                                    └──────────────────► Phase 11 (RP2) ◄───────────────┘
                                                              │
                                                              ▼
                                                    Phase 12 (Secondary Ports)
                                                              │
                                                              ▼
                                                    Phase 13 (Testing)
                                                              │
                                                              ▼
                                                    Phase 14 (Documentation)
```

---

## Estimated Task Distribution

### [HAIKU] Tasks (Simple, well-defined)

| Phase | Task | Est. Lines |
|-------|------|-----------|
| 1.1 | Create directory structure | ~200 (boilerplate) |
| 1.2 | FreeRTOSConfig_template.h | ~150 |
| 1.3 | Core data structures | ~80 |
| 2.1 | freertos.mk | ~50 |
| 2.2 | freertos.cmake | ~40 |
| 3.1 | mp_freertos_hal.h | ~30 |
| 4.1 | Thread state functions | ~20 |
| 4.2 | mp_thread_get_id() | ~5 |
| 5.2 | Recursive mutex | ~30 |
| 6.1 | Global thread list | ~20 |
| 6.6 | mp_thread_start/finish | ~20 |
| 8.1 | GIL exit with yield | ~15 |
| 9.1 | Service task API | ~100 |
| 10.1 | STM32 FreeRTOSConfig.h | ~100 |
| 10.3 | STM32 mpconfigport.h | ~20 |
| 10.6 | Static allocation callbacks | ~40 |
| 11.1 | RP2 FreeRTOSConfig.h | ~100 |
| 11.3 | RP2 mpconfigport.h | ~20 |
| 13.1 | Run thread tests | N/A |
| 13.2 | stress_freertos_gc.py | ~80 |
| 14.1 | Update port READMEs | ~100 |
| 14.2 | extmod/freertos/README.md | ~200 |
| 14.3 | Update CODECONVENTIONS.md | ~20 |

**Total HAIKU:** ~1,440 lines + test execution

### [REGULAR] Tasks (Complex, requires analysis)

| Phase | Task | Est. Lines | Complexity |
|-------|------|-----------|------------|
| 3.2 | mp_freertos_hal.c | ~100 | Medium |
| 5.1 | Basic mutex | ~80 | Medium |
| 6.2 | mp_thread_init() | ~60 | Medium |
| 6.3 | Entry wrapper | ~80 | High |
| 6.4 | mp_thread_create() | ~150 | High |
| 6.5 | Reaper mechanism | ~80 | Medium |
| 7.1 | mp_thread_gc_others() | ~60 | High |
| 9.2 | Service registration | ~200 | Medium |
| 10.2 | STM32 Makefile | ~50 | Medium |
| 10.4 | STM32 main.c | ~150 | High |
| 10.5 | Disable pybthread | ~20 | Low |
| 11.2 | RP2 CMakeLists.txt | ~80 | Medium |
| 11.4 | RP2 main.c services | ~200 | High |
| 11.5 | RP2 service registration | ~100 | Medium |
| 12.1 | mimxrt integration | ~150 | Medium |
| 12.2 | SAMD51 integration | ~150 | Medium |
| 12.3 | nRF52840 integration | ~180 | Medium |
| 13.3 | Run stress tests | N/A | Medium |
| 13.4 | QEMU CI integration | ~100 | Medium |
| 14.4 | Final cleanup | N/A | Low |

**Total REGULAR:** ~1,990 lines + testing

### [REVIEW] Checkpoints

| Phase | Review Point | Focus |
|-------|-------------|-------|
| 2.3 | Build system | Conditional compilation |
| 5.3 | Mutex implementation | Deadlock, priority inversion |
| 6.7 | Thread lifecycle | Memory leaks, race conditions |
| 7.2 | GC integration | Root scanning completeness |
| 9.3 | Service framework | Soft reset, race conditions |
| 10.7 | STM32 integration | Full functionality |
| 11.6 | RP2 integration | Dual-core, USB responsiveness |
| 13.5 | Final review | Code quality, completeness |

---

## Risk Areas

### High Risk (Require extra attention)

1. **GC Stack Scanning (7.1)** - Incorrect implementation causes silent corruption
2. **Thread Creation Memory (6.4)** - Must handle all failure modes
3. **Entry Wrapper (6.3)** - Exception handling, cleanup on failure
4. **STM32 main.c Changes (10.4)** - Must not break existing functionality

### Medium Risk

1. **Mutex Implementation (5.1)** - Pre-scheduler operation
2. **RP2 Service Tasks (11.4)** - Pico SDK integration complexity
3. **nRF SoftDevice Coexistence (12.3)** - Interrupt priority conflicts

### Low Risk

1. **Build system fragments** - Well-defined patterns exist
2. **Data structures** - Direct from spec
3. **Documentation** - Straightforward

---

## Success Criteria

Per Requirements Section 17:

1. [ ] All `extmod/freertos/` files implemented
2. [ ] Code passes `tools/codeformat.py`
3. [ ] Build system fragments work for Make and CMake
4. [ ] GC correctly scans all thread stacks
5. [ ] Thread cleanup (reaper) works correctly
6. [ ] Memory does not leak over extended operation
7. [ ] STM32 passes all `tests/thread/*.py` tests
8. [ ] RP2 passes all `tests/thread/*.py` tests
9. [ ] mimxrt builds with `MICROPY_PY_THREAD=1`
10. [ ] SAMD51 builds with `MICROPY_PY_THREAD=1`
11. [ ] nRF52840 builds with `MICROPY_PY_THREAD=1`
12. [ ] QEMU ARM configuration available for CI
13. [ ] FreeRTOS POSIX simulator documented
14. [ ] `stress_freertos_gc.py` passes on all ports
15. [ ] Integration guide updated for all target ports
16. [ ] FreeRTOSConfig.h template documented
