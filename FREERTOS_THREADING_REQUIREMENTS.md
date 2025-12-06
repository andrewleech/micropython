# MicroPython FreeRTOS Threading Backend: Technical Requirements Specification

**Document Version:** 1.5
**Date:** 2025-12-06
**Status:** Requirements Definition
**Derived From:** gem-freertos.md transcript, zephyr-threading-memory-architecture.md, and codebase analysis

**Changelog:**
- v1.5: Added nRF port support (nRF52840 recommended, nRF52832 optional, nRF51 excluded due to RAM)
- v1.4: Added conditional compilation requirement (Section 2.2) - all threading guarded by MICROPY_PY_THREAD
- v1.3: Added generalized service task framework (Section 13.8), updated directory structure (Section 2.1)
- v1.2: Added RP2 background service task architecture (Section 13.3-13.7), micropythonrt reference
- v1.1: Added configuration cascade architecture (Section 2.4), expanded Section 12 configuration documentation

---

## 1. Feature Overview

### 1.1 Purpose
Create a common, reusable FreeRTOS-based `_thread` module implementation for MicroPython that can be shared across multiple ports. This replaces the current fragmented approach where each port implements its own threading backend from scratch.

### 1.2 Current State Analysis

| Port | Current Implementation | Threading Model | CPU |
|------|----------------------|-----------------|-----|
| **ESP32** | `ports/esp32/mpthreadport.c` | FreeRTOS via ESP-IDF (xTaskCreatePinnedToCore) | Xtensa/RISC-V |
| **CC3200** | `ports/cc3200/mpthreadport.c` | FreeRTOS (vendor SDK) - obsolete port | Cortex-M4 |
| **STM32** | `ports/stm32/pybthread.c` | Custom cooperative round-robin scheduler using PendSV | Cortex-M4/M7 |
| **RP2** | `ports/rp2/mpthreadport.c` | Pico SDK multicore (limited to 2 threads, one per core) | Dual Cortex-M0+ |
| **mimxrt** | None | No threading support | Cortex-M7 |
| **samd** | None | No threading support | Cortex-M0+/M4 |
| **nRF** | None | No threading support | Cortex-M0/M4/M33 |
| **Zephyr** | `ports/zephyr/mpthreadport.c` | Zephyr kernel (k_thread_create) - **OUT OF SCOPE** | Various |
| **Unix** | `ports/unix/mpthreadport.c` | POSIX pthreads | Host |
| **Windows** | None | No threading support | Host |
| **QEMU** | N/A | No threading currently | Emulated ARM |

### 1.3 Target Ports for FreeRTOS Backend

| Port | Priority | FreeRTOS Port | RAM | Notes |
|------|----------|---------------|-----|-------|
| **STM32** | Primary | ARM_CM4F/CM7 | 128KB-1MB | Replace custom cooperative scheduler |
| **RP2** | Primary | ARM_CM0 (SMP) | 264KB | Multiple threads + background service tasks (see below) |
| **mimxrt** | Secondary | ARM_CM7 | 256KB-1MB | Clean slate - no existing threading to migrate |
| **samd** | Tertiary | ARM_CM0/CM4F | 32KB-256KB | RAM-constrained on M0+ variants |
| **nRF** | Secondary | ARM_CM0/CM4F/CM33 | 16KB-256KB | BLE service tasks; nRF52840 recommended |
| **QEMU ARM** | Testing | ARM_CM* | Unlimited | CI testing infrastructure |
| **ESP32** | Optional | N/A (use ESP-IDF) | 320KB+ | Already works; consolidation benefit only |

**RP2 Enhanced Threading Goals:**

The current RP2 port is limited to 2 threads (one per core). FreeRTOS enables:
1. Multiple smaller Python threads (not limited by core count)
2. Background service tasks for BLE, WiFi, lwIP, TinyUSB at higher priority
3. Priority-based preemption so services remain responsive during Python execution

Reference implementation: [micropythonrt](https://github.com/gneverov/micropythonrt) - demonstrates FreeRTOS integration with separate tasks for USB, networking, and MicroPython. See Section 13 for detailed architecture.

### 1.4 Out of Scope

| Port | Reason |
|------|--------|
| **Zephyr** | Uses its own kernel - should NOT be changed. |
| **Unix** | POSIX pthreads is the native, correct API. FreeRTOS would add unnecessary overhead with no benefit. |
| **Windows** | Win32 threading is the native API. Same reasoning as Unix. |

### 1.5 SAMD Port Considerations

The SAMD family spans a wide range of capabilities:

| MCU | Core | RAM | Threading Viability |
|-----|------|-----|---------------------|
| SAMD21 | Cortex-M0+ | 32KB | Marginal - 1-2 threads max with 4KB stacks |
| SAMD51 | Cortex-M4 | 192KB-256KB | Good - several threads feasible |

For SAMD21, threading may be impractical due to RAM constraints. Consider making threading optional per-board via `mpconfigboard.h`.

---

## 2. Architecture Requirements

### 2.1 Directory Structure

```
extmod/freertos/
├── mpthreadport.h            # Threading API (py/mpthread.h backend)
├── mpthreadport.c            # Threading implementation
├── mp_freertos_service.h     # Service task framework API (optional)
├── mp_freertos_service.c     # Service task framework implementation
├── mp_freertos_hal.h         # HAL helpers (delay, critical sections)
├── mp_freertos_hal.c         # HAL implementation
├── freertos.mk               # Makefile fragment for kernel integration
├── freertos.cmake            # CMake fragment for kernel integration
└── FreeRTOSConfig_template.h # Template config for ports to customize

ports/<port>/
├── mpserviceport.c           # Port-specific service registration (optional)
└── FreeRTOSConfig.h          # Port-specific FreeRTOS config
```

**Components:**
- **Threading** (`mpthreadport.*`): Core `_thread` module implementation - required when threading enabled
- **Service Framework** (`mp_freertos_service.*`): Background task management - optional, for ports with USB/network/BLE
- **HAL Helpers** (`mp_freertos_hal.*`): Common delay/critical section implementations

### 2.2 Conditional Compilation Requirement

**All threading functionality MUST be guarded by `MICROPY_PY_THREAD`.**

When `MICROPY_PY_THREAD=0` (the default), builds MUST work exactly as they do today - no FreeRTOS dependency, no threading overhead, no behavior changes. This is a hard requirement for backwards compatibility.

```c
// Example: All FreeRTOS threading code must be conditional
#if MICROPY_PY_THREAD
#include "extmod/freertos/mpthreadport.h"
// ... threading code ...
#endif

// Example: Service framework also conditional
#if MICROPY_PY_THREAD && MICROPY_FREERTOS_SERVICE_TASKS
#include "extmod/freertos/mp_freertos_service.h"
// ... service task code ...
#endif
```

**Implication for ports:**
- Existing non-threaded builds remain unchanged
- Threading is opt-in via `MICROPY_PY_THREAD=1` in `mpconfigport.h` or `mpconfigboard.h`
- Service framework is opt-in via `MICROPY_FREERTOS_SERVICE_TASKS=1` (defaults to 1 when threading enabled)
- Ports can enable threading without the service framework if desired

**Build system guards:**
```makefile
# freertos.mk - only include sources when threading enabled
ifeq ($(MICROPY_PY_THREAD),1)
SRC_MOD += extmod/freertos/mpthreadport.c
# ... FreeRTOS kernel sources ...
endif
```

### 2.3 Interface Contract

The implementation MUST conform to the existing `py/mpthread.h` API:

```c
// Required functions (from py/mpthread.h)
struct _mp_state_thread_t *mp_thread_get_state(void);
void mp_thread_set_state(struct _mp_state_thread_t *state);
mp_uint_t mp_thread_create(void *(*entry)(void *), void *arg, size_t *stack_size);
mp_uint_t mp_thread_get_id(void);
void mp_thread_start(void);
void mp_thread_finish(void);
void mp_thread_mutex_init(mp_thread_mutex_t *mutex);
int mp_thread_mutex_lock(mp_thread_mutex_t *mutex, int wait);
void mp_thread_mutex_unlock(mp_thread_mutex_t *mutex);

// Optional recursive mutex (MICROPY_PY_THREAD_RECURSIVE_MUTEX)
void mp_thread_recursive_mutex_init(mp_thread_recursive_mutex_t *mutex);
int mp_thread_recursive_mutex_lock(mp_thread_recursive_mutex_t *mutex, int wait);
void mp_thread_recursive_mutex_unlock(mp_thread_recursive_mutex_t *mutex);
```

### 2.4 Configuration Cascade Architecture

MicroPython uses a layered configuration system. Understanding this cascade is essential for correctly integrating the FreeRTOS backend.

#### 2.4.1 Include Hierarchy

```
py/mpconfig.h
    ├── Includes mpconfigport.h (or MP_CONFIGFILE if defined)
    │       ├── Includes mpconfigboard.h (board-specific hardware)
    │       └── Includes mpconfigboard_common.h (shared defaults, if present)
    └── Defines defaults for any unset options

py/mphal.h
    └── Includes mphalport.h (or MICROPY_MPHALPORT_H if defined)

py/mpthread.h
    └── Includes mpthreadport.h (or MICROPY_MPTHREADPORT_H if defined)
```

#### 2.4.2 Configuration Layers

| Layer | File Location | Responsibility |
|-------|--------------|----------------|
| **Core** | `py/mpconfig.h` | Default values for ALL configuration options |
| **Port** | `ports/<port>/mpconfigport.h` | Port-wide defaults (e.g., `MICROPY_PY_THREAD (0)`) |
| **Board** | `ports/<port>/boards/<board>/mpconfigboard.h` | Hardware-specific overrides |
| **Variant** | `mpconfigvariant_*.mk` | Build-time CFLAGS (e.g., `-DMICROPY_PY_THREAD=1`) |

**Resolution Order:** Board → Port → Core (first definition wins)

#### 2.4.3 Backend Selection Mechanism

The threading backend is selected via `MICROPY_MPTHREADPORT_H` in `py/mpthread.h:35-39`:

```c
// py/mpthread.h
#ifdef MICROPY_MPTHREADPORT_H
#include MICROPY_MPTHREADPORT_H  // Custom path from mpconfigport.h
#else
#include <mpthreadport.h>         // Default: search port include path
#endif
```

#### 2.4.4 Existing Port Patterns

| Port | Threading Backend | Selection Method |
|------|-------------------|------------------|
| **ESP32** | FreeRTOS via ESP-IDF | Default `mpthreadport.h` in port directory |
| **STM32** | Custom `pybthread.h` | `mpthreadport.h` wraps `pybthread.h` |
| **RP2** | Pico SDK mutexes | `mpthreadport.h` uses `mutex_extra.h` |
| **Unix** | POSIX pthreads | `mpthreadport.h` uses `pthread.h` |

#### 2.4.5 FreeRTOS Backend Integration

To use the universal FreeRTOS backend, a port/board must define:

**In `mpconfigport.h` or `mpconfigboard.h`:**
```c
// Enable threading
#define MICROPY_PY_THREAD (1)
#define MICROPY_PY_THREAD_GIL (1)

// Select FreeRTOS backend (overrides default port header)
#define MICROPY_MPTHREADPORT_H "extmod/freertos/mpthreadport.h"

// RTOS-aware delay (recommended - prevents GIL starvation)
#define mp_hal_delay_ms mp_freertos_delay_ms
```

**In `mphalport.h` (atomic section implementation):**
```c
// Option A: Use FreeRTOS critical sections (preferred for thread safety)
#include "FreeRTOS.h"
#include "task.h"
#define MICROPY_BEGIN_ATOMIC_SECTION() ({ \
    taskENTER_CRITICAL(); \
    0; /* return value unused but required */ \
})
#define MICROPY_END_ATOMIC_SECTION(state) taskEXIT_CRITICAL()

// Option B: Use interrupt disable (simpler, works without scheduler)
#define MICROPY_BEGIN_ATOMIC_SECTION()     disable_irq()
#define MICROPY_END_ATOMIC_SECTION(state)  enable_irq(state)
```

#### 2.4.6 HAL Integration Points

The following `mphalport.h` hooks interact with threading:

| Macro | Purpose | FreeRTOS Implementation |
|-------|---------|-------------------------|
| `MICROPY_BEGIN_ATOMIC_SECTION()` | Disable context switches | `taskENTER_CRITICAL()` or IRQ disable |
| `MICROPY_END_ATOMIC_SECTION(state)` | Re-enable context switches | `taskEXIT_CRITICAL()` or IRQ enable |
| `mp_hal_delay_ms(ms)` | Millisecond delay | `vTaskDelay(pdMS_TO_TICKS(ms))` |
| `mp_hal_delay_us(us)` | Microsecond delay | Busy-wait or DWT cycle counter |
| `MICROPY_EVENT_POLL_HOOK` | Background processing | GIL release + `vTaskDelay(1)` |

#### 2.4.7 Thread State Access

Thread-local state is accessed via `mp_thread_get_state()` / `mp_thread_set_state()`, which maps to:

```c
// FreeRTOS implementation using Thread Local Storage
#define MP_FREERTOS_TLS_INDEX 0  // Configure in FreeRTOSConfig.h

mp_state_thread_t *mp_thread_get_state(void) {
    return pvTaskGetThreadLocalStoragePointer(NULL, MP_FREERTOS_TLS_INDEX);
}

void mp_thread_set_state(mp_state_thread_t *state) {
    vTaskSetThreadLocalStoragePointer(NULL, MP_FREERTOS_TLS_INDEX, state);
}
```

**FreeRTOSConfig.h requirement:**
```c
#define configNUM_THREAD_LOCAL_STORAGE_POINTERS (1)  // At least 1
```

---

## 3. Memory Management Requirements (CRITICAL)

### 3.1 The Core Problem

FreeRTOS and MicroPython have incompatible memory models that MUST be reconciled:

| Aspect | FreeRTOS Default | MicroPython GC |
|--------|-----------------|----------------|
| Heap | `pvPortMalloc()` (separate heap) | `gc_alloc()` / `m_new()` |
| Stack allocation | Internal to xTaskCreate | Must be visible to GC |
| Object lifetime | Manual free | Automatic garbage collection |

**Critical Issue:** If a Python object is referenced ONLY from a thread's stack, and that stack is allocated from the FreeRTOS heap (invisible to GC), the GC will collect the object while it's still in use, causing use-after-free crashes.

### 3.2 Solution: "GC-First" Memory Strategy

The implementation MUST use MicroPython's allocator for ALL thread-related memory:

1. **Thread Control Block (TCB)**: Allocate via `m_new(StaticTask_t, 1)`
2. **Thread Stack**: Allocate via `m_new(StackType_t, stack_len)`
3. **Thread Metadata**: Allocate via `m_new_obj(mp_thread_t)`
4. **Creation API**: Use ONLY `xTaskCreateStatic()` (NOT `xTaskCreate`)

This approach was validated in the Zephyr threading integration (see `zephyr-threading-memory-architecture.md` Section 3).

### 3.3 Memory Alignment Requirements

ARM AAPCS requires 8-byte stack alignment. MicroPython's GC provides 16-byte aligned blocks:
```
MICROPY_BYTES_PER_GC_BLOCK = 4 × sizeof(mp_uint_t) = 16 bytes (32-bit ARM)
```

All `gc_alloc()` returns are block-aligned, satisfying alignment requirements.

### 3.4 Thread Stack Allocation Algorithm

```c
static void *mp_thread_stack_alloc(size_t size) {
    // Round up to alignment requirement
    size = (size + MP_THREAD_STACK_ALIGN - 1) & ~(MP_THREAD_STACK_ALIGN - 1);
    return m_new(StackType_t, size / sizeof(StackType_t));
}
```

### 3.5 GC Root Registration

The thread list MUST be registered as a GC root pointer:
```c
MP_REGISTER_ROOT_POINTER(struct _mp_thread_t *mp_thread_list_head);
```

---

## 4. Thread Lifecycle Management

### 4.1 State Machine

```
┌─────────────┐
│  ALLOCATED  │ ◄── mp_thread_create() allocates memory
└──────┬──────┘
       │ xTaskCreateStatic() success
       ▼
┌─────────────┐
│   RUNNING   │ ◄── Thread executing Python code
└──────┬──────┘
       │ Entry function returns
       ▼
┌─────────────┐
│  FINISHED   │ ◄── Thread waiting to be reaped
└──────┬──────┘
       │ Reaper detects FINISHED state
       ▼
┌─────────────┐
│  RECLAIMED  │ ◄── Memory freed via m_del()
└─────────────┘
```

### 4.2 Lazy Cleanup ("Reaper") Mechanism

**Problem:** A FreeRTOS task cannot free its own stack while running on it.

**Solution:** Implement lazy cleanup where finished threads are reaped by the NEXT thread creation or by a dedicated cleanup hook.

**Algorithm:**
```c
void mp_thread_reap_dead_threads(void) {
    mp_thread_mutex_lock(&thread_mutex, 1);

    mp_thread_t **prev_ptr = &mp_thread_list_head;
    mp_thread_t *curr = mp_thread_list_head;

    while (curr != NULL) {
        if (curr->state == MP_THREAD_STATE_FINISHED) {
            // Unlink from list
            *prev_ptr = curr->next;
            mp_thread_t *to_free = curr;
            curr = curr->next;

            // Free resources
            m_del(StackType_t, to_free->stack, to_free->stack_len);
            m_del(StaticTask_t, to_free->tcb, 1);
            m_del_obj(mp_thread_t, to_free);
        } else {
            prev_ptr = &curr->next;
            curr = curr->next;
        }
    }

    mp_thread_mutex_unlock(&thread_mutex);
}
```

### 4.3 Thread Entry Wrapper

The entry wrapper handles TLS setup and exit protocol:

```c
static void freertos_entry_wrapper(void *arg) {
    mp_thread_t *th = (mp_thread_t *)arg;

    // 1. Set Thread-Local Storage for mp_state_thread_t
    mp_state_thread_t ts;
    mp_thread_set_state(&ts);
    vTaskSetThreadLocalStoragePointer(NULL, MP_FREERTOS_TLS_INDEX, &ts);

    // 2. Signal thread is starting
    mp_thread_start();

    // 3. Execute Python entry function
    th->entry(th->arg);

    // 4. Mark thread as finished
    mp_thread_mutex_lock(&thread_mutex, 1);
    th->state = MP_THREAD_STATE_FINISHED;
    mp_thread_mutex_unlock(&thread_mutex);

    // 5. Eternal yield (cannot return, cannot free own stack)
    for (;;) {
        vTaskDelay(portMAX_DELAY);
    }
}
```

---

## 5. FreeRTOS Configuration Requirements

### 5.1 Mandatory Configuration (`FreeRTOSConfig.h`)

| Macro | Required Value | Rationale |
|-------|---------------|-----------|
| `configSUPPORT_STATIC_ALLOCATION` | `1` | **CRITICAL.** Enables `xTaskCreateStatic()` for GC-first memory strategy. |
| `configNUM_THREAD_LOCAL_STORAGE_POINTERS` | `≥ 1` | Store `mp_state_thread_t*` for each thread. |
| `configUSE_MUTEXES` | `1` | Required for `mp_thread_mutex_*` functions. |
| `configUSE_RECURSIVE_MUTEXES` | `1` | Required for recursive mutex support (GIL). |
| `INCLUDE_vTaskDelete` | `1` | Required for thread cleanup on exit. |
| `INCLUDE_xTaskGetCurrentTaskHandle` | `1` | Required for `mp_thread_get_id()` and main thread adoption. |

### 5.2 Recommended Configuration

| Macro | Recommended Value | Rationale |
|-------|------------------|-----------|
| `configTICK_RATE_HZ` | `1000` | 1ms tick for `time.sleep_ms()` precision. |
| `configMAX_PRIORITIES` | `≥ 4` | Separate Idle, Main, Python threads, Drivers. |
| `configMINIMAL_STACK_SIZE` | `128` (words) | Minimum for context switch. |
| `INCLUDE_uxTaskGetStackHighWaterMark` | `1` | Stack usage debugging. |
| `configUSE_TASK_NOTIFICATIONS` | `1` | Efficient signaling. |

### 5.3 Callback Function Requirements

When `configSUPPORT_STATIC_ALLOCATION = 1`, ports MUST implement:
```c
void vApplicationGetIdleTaskMemory(StaticTask_t **ppxIdleTaskTCBBuffer,
                                   StackType_t **ppxIdleTaskStackBuffer,
                                   configSTACK_DEPTH_TYPE *puxIdleTaskStackSize);

void vApplicationGetTimerTaskMemory(StaticTask_t **ppxTimerTaskTCBBuffer,
                                    StackType_t **ppxTimerTaskStackBuffer,
                                    configSTACK_DEPTH_TYPE *puxTimerTaskStackSize);
```

---

## 6. Data Structure Definitions

### 6.1 Thread Structure (`mp_thread_t`)

```c
typedef enum {
    MP_THREAD_STATE_NEW = 0,
    MP_THREAD_STATE_RUNNING,
    MP_THREAD_STATE_FINISHED,
} mp_thread_state_t;

typedef struct _mp_thread_t {
    // FreeRTOS Objects
    TaskHandle_t id;              // FreeRTOS task handle
    StaticTask_t *tcb;            // Pointer to GC-allocated TCB

    // Memory Management
    void *stack;                  // Pointer to GC-allocated stack buffer
    size_t stack_len;             // Stack length in words (for GC scanning)

    // Python State
    void *arg;                    // Entry function argument (GC root)
    void *(*entry)(void *);       // Entry function pointer

    // Lifecycle
    volatile mp_thread_state_t state;  // Current state

    // Linked List
    struct _mp_thread_t *next;    // Next thread in global list
} mp_thread_t;
```

### 6.2 Mutex Structure (`mp_thread_mutex_t`)

```c
typedef struct _mp_thread_mutex_t {
    StaticSemaphore_t static_sem; // Static storage for semaphore
    SemaphoreHandle_t handle;     // FreeRTOS handle
} mp_thread_mutex_t;

typedef struct _mp_thread_recursive_mutex_t {
    StaticSemaphore_t static_sem;
    SemaphoreHandle_t handle;
} mp_thread_recursive_mutex_t;
```

---

## 7. GC Integration Requirements

### 7.1 `mp_thread_gc_others()` Implementation

This function MUST scan all thread stacks for GC roots:

```c
void mp_thread_gc_others(void) {
    mp_thread_mutex_lock(&thread_mutex, 1);

    TaskHandle_t current = xTaskGetCurrentTaskHandle();

    for (mp_thread_t *th = mp_thread_list_head; th != NULL; th = th->next) {
        // Always scan the thread structure itself
        gc_collect_root((void **)&th, 1);
        gc_collect_root(&th->arg, 1);

        // Skip current thread (its stack is being traced normally)
        if (th->id == current) {
            continue;
        }

        // Only scan stack of running threads
        if (th->state != MP_THREAD_STATE_RUNNING) {
            continue;
        }

        // Scan entire stack buffer
        gc_collect_root(th->stack, th->stack_len);
    }

    mp_thread_mutex_unlock(&thread_mutex);
}
```

### 7.2 Main Thread Stack Handling

The main thread's stack is typically statically allocated (not in GC heap) and is scanned via linker symbols (`__StackTop`, `__StackBottom`). The FreeRTOS backend MUST:

1. Register the main thread in `mp_thread_list_head` during `mp_thread_init()`
2. Set `main_thread->stack = NULL` to indicate special handling
3. Skip stack scanning for threads with `stack == NULL` (handled elsewhere)

---

## 8. HAL Integration Requirements

### 8.1 Delay Functions

When threading is enabled, `mp_hal_delay_ms()` MUST yield to the scheduler:

```c
void mp_freertos_delay_ms(mp_uint_t ms) {
    if (ms > 0) {
        vTaskDelay(pdMS_TO_TICKS(ms));
    } else {
        taskYIELD();
    }
}

// Port configures:
#define mp_hal_delay_ms mp_freertos_delay_ms
```

### 8.2 Tick Functions

`mp_hal_ticks_ms()` should use FreeRTOS tick count for consistency:
```c
mp_uint_t mp_hal_ticks_ms(void) {
    return xTaskGetTickCount() * portTICK_PERIOD_MS;
}
```

### 8.3 GIL Integration

The Global Interpreter Lock uses the mutex implementation. The GIL release MUST yield:

```c
void mp_thread_gil_exit(void) {
    mp_thread_mutex_unlock(&MP_STATE_VM(gil_mutex));
    taskYIELD();  // Critical: allow other threads to acquire GIL
}

#define MP_THREAD_GIL_EXIT() mp_thread_gil_exit()
```

---

## 9. Build System Integration

### 9.1 Makefile Fragment (`extmod/freertos/freertos.mk`)

```makefile
# extmod/freertos/freertos.mk
# Required input: FREERTOS_DIR (path to FreeRTOS kernel source)

ifndef FREERTOS_DIR
$(error FREERTOS_DIR must be defined to use FreeRTOS threading)
endif

# FreeRTOS kernel sources (architecture-independent)
FREERTOS_SRC_C = \
    $(FREERTOS_DIR)/tasks.c \
    $(FREERTOS_DIR)/queue.c \
    $(FREERTOS_DIR)/list.c \
    $(FREERTOS_DIR)/timers.c \
    $(FREERTOS_DIR)/event_groups.c \
    $(FREERTOS_DIR)/stream_buffer.c \
    $(FREERTOS_DIR)/portable/MemMang/heap_4.c

# MicroPython FreeRTOS backend
FREERTOS_SRC_C += extmod/freertos/mpthreadport.c

# Include paths
FREERTOS_INC = \
    -I$(FREERTOS_DIR)/include \
    -Iextmod/freertos

# Add to build
SRC_MOD += $(FREERTOS_SRC_C)
CFLAGS_MOD += $(FREERTOS_INC)

# NOTE: Port must add architecture-specific port.c, e.g.:
# SRC_C += $(FREERTOS_DIR)/portable/GCC/ARM_CM4F/port.c
```

### 9.2 CMake Fragment (`extmod/freertos/freertos.cmake`)

```cmake
# extmod/freertos/freertos.cmake
# Required: FREERTOS_DIR variable must be set

if(NOT DEFINED FREERTOS_DIR)
    message(FATAL_ERROR "FREERTOS_DIR must be defined")
endif()

add_library(micropython_freertos INTERFACE)

target_sources(micropython_freertos INTERFACE
    ${FREERTOS_DIR}/tasks.c
    ${FREERTOS_DIR}/queue.c
    ${FREERTOS_DIR}/list.c
    ${FREERTOS_DIR}/timers.c
    ${FREERTOS_DIR}/event_groups.c
    ${FREERTOS_DIR}/stream_buffer.c
    ${FREERTOS_DIR}/portable/MemMang/heap_4.c
    ${CMAKE_CURRENT_LIST_DIR}/mpthreadport.c
)

target_include_directories(micropython_freertos INTERFACE
    ${FREERTOS_DIR}/include
    ${CMAKE_CURRENT_LIST_DIR}
)

# NOTE: Port must add architecture-specific port files to the target
```

---

## 10. Port-Specific Integration Guide

### 10.1 STM32 Port Integration Steps

1. **Add FreeRTOS Kernel Submodule:**
   ```bash
   git submodule add https://github.com/FreeRTOS/FreeRTOS-Kernel lib/FreeRTOS-Kernel
   ```

2. **Create `ports/stm32/FreeRTOSConfig.h`:**
   - Copy template from `extmod/freertos/FreeRTOSConfig_template.h`
   - Configure for specific STM32 family (clock, memory)

3. **Update `ports/stm32/Makefile`:**
   ```makefile
   MICROPY_PY_THREAD = 1
   FREERTOS_DIR = $(TOP)/lib/FreeRTOS-Kernel

   include $(TOP)/extmod/freertos/freertos.mk

   # Architecture-specific port (select based on MCU)
   ifeq ($(MCU_SERIES),$(filter $(MCU_SERIES),f4 f7 h7))
   SRC_C += $(FREERTOS_DIR)/portable/GCC/ARM_CM4F/port.c
   CFLAGS_MOD += -I$(FREERTOS_DIR)/portable/GCC/ARM_CM4F
   endif
   ```

4. **Update `ports/stm32/mpconfigport.h`:**
   ```c
   #define MICROPY_PY_THREAD (1)
   #define MICROPY_PY_THREAD_GIL (1)
   #define MICROPY_MPTHREADPORT_H "extmod/freertos/mpthreadport.h"
   #define mp_hal_delay_ms mp_freertos_delay_ms
   ```

5. **Modify `ports/stm32/main.c` Startup:**
   ```c
   // Option A: Start scheduler after hardware init
   int main(void) {
       hardware_init();
       xTaskCreate(mp_main_task, "MP", MP_MAIN_STACK_SIZE, NULL, MP_MAIN_PRIORITY, NULL);
       vTaskStartScheduler();
       // Never returns
   }

   // Option B: Register current context as main task (like Zephyr approach)
   ```

6. **Remove/Disable `pybthread.c`:**
   - Remove from build or conditionally compile based on `MICROPY_PY_THREAD_FREERTOS`

### 10.2 RP2 Port Integration Steps

Similar to STM32, but using FreeRTOS-SMP for dual-core support:
1. Use `FreeRTOS-Kernel-SMP` variant
2. Configure `configNUMBER_OF_CORES = 2`
3. Consider core affinity for main MicroPython task

### 10.3 mimxrt Port Integration Steps

The mimxrt port targets NXP i.MX RT series (RT1010, RT1050, RT1060, RT1170). These are high-performance Cortex-M7 chips with ample RAM.

1. **Add FreeRTOS Kernel Submodule** (if not using NXP SDK's bundled version):
   ```bash
   git submodule add https://github.com/FreeRTOS/FreeRTOS-Kernel lib/FreeRTOS-Kernel
   ```

2. **Create `ports/mimxrt/FreeRTOSConfig.h`:**
   - Configure for 600MHz+ Cortex-M7
   - Set `configCPU_CLOCK_HZ` appropriately per board
   - Enable `configUSE_TICKLESS_IDLE` for power savings (optional)

3. **Update `ports/mimxrt/Makefile`:**
   ```makefile
   MICROPY_PY_THREAD = 1
   FREERTOS_DIR = $(TOP)/lib/FreeRTOS-Kernel

   include $(TOP)/extmod/freertos/freertos.mk

   # Cortex-M7 with FPU (all mimxrt variants)
   SRC_C += $(FREERTOS_DIR)/portable/GCC/ARM_CM7/r0p1/port.c
   CFLAGS_MOD += -I$(FREERTOS_DIR)/portable/GCC/ARM_CM7/r0p1
   ```

4. **Update `ports/mimxrt/mpconfigport.h`:**
   ```c
   #define MICROPY_PY_THREAD (1)
   #define MICROPY_PY_THREAD_GIL (1)
   #define MICROPY_MPTHREADPORT_H "extmod/freertos/mpthreadport.h"
   #define mp_hal_delay_ms mp_freertos_delay_ms
   ```

5. **Board-Specific Stack Sizes:**
   - RT1010 (128KB RAM): `MP_FREERTOS_DEFAULT_STACK_SIZE = 4096`
   - RT1060 (1MB RAM): `MP_FREERTOS_DEFAULT_STACK_SIZE = 8192`

### 10.4 SAMD Port Integration Steps

The SAMD port covers both SAMD21 (Cortex-M0+, 32KB RAM) and SAMD51 (Cortex-M4, 192KB+ RAM).

**SAMD51 (Recommended):**

1. **Add FreeRTOS Kernel Submodule:**
   ```bash
   git submodule add https://github.com/FreeRTOS/FreeRTOS-Kernel lib/FreeRTOS-Kernel
   ```

2. **Create `ports/samd/FreeRTOSConfig.h`:**
   ```c
   #define configCPU_CLOCK_HZ (120000000)  // SAMD51 max frequency
   #define configTICK_RATE_HZ (1000)
   #define configMINIMAL_STACK_SIZE (128)  // words
   #define configTOTAL_HEAP_SIZE (0)       // Not used - static allocation only

   #define configSUPPORT_STATIC_ALLOCATION (1)
   #define configSUPPORT_DYNAMIC_ALLOCATION (0)  // GC-first strategy

   // RAM-conscious settings
   #define configMAX_PRIORITIES (4)
   #define configMAX_TASK_NAME_LEN (8)
   ```

3. **Update `ports/samd/Makefile`:**
   ```makefile
   # Only enable threading for SAMD51 boards by default
   ifeq ($(MCU_SERIES),SAMD51)
   MICROPY_PY_THREAD = 1
   FREERTOS_DIR = $(TOP)/lib/FreeRTOS-Kernel

   include $(TOP)/extmod/freertos/freertos.mk

   SRC_C += $(FREERTOS_DIR)/portable/GCC/ARM_CM4F/port.c
   CFLAGS_MOD += -I$(FREERTOS_DIR)/portable/GCC/ARM_CM4F
   endif
   ```

**SAMD21 (Optional, RAM-constrained):**

For boards with sufficient RAM (e.g., external SRAM):
```makefile
# In mpconfigboard.mk for specific board
MICROPY_PY_THREAD = 1
MP_FREERTOS_DEFAULT_STACK_SIZE = 2048  # Minimal stack
MP_FREERTOS_MAX_THREADS = 2            # Limit thread count
```

Note: SAMD21's Cortex-M0+ requires `ARM_CM0` FreeRTOS port, which lacks some optimizations available on CM4F.

### 10.5 nRF Port Integration Steps

The nRF port covers nRF51 (Cortex-M0, 16-32KB RAM), nRF52832 (Cortex-M4, 64KB RAM), nRF52840 (Cortex-M4, 256KB RAM), and nRF9160 (Cortex-M33, 256KB RAM).

**nRF52840 (Recommended):**

The nRF52840 is the best candidate with 256KB RAM, USB support, and BLE.

1. **Add FreeRTOS Kernel Submodule** (if not using Nordic SDK's bundled version):
   ```bash
   git submodule add https://github.com/FreeRTOS/FreeRTOS-Kernel lib/FreeRTOS-Kernel
   ```

2. **Create `ports/nrf/FreeRTOSConfig.h`:**
   ```c
   #define configCPU_CLOCK_HZ (64000000)  // nRF52840 max frequency
   #define configTICK_RATE_HZ (1000)
   #define configMINIMAL_STACK_SIZE (128)  // words
   #define configTOTAL_HEAP_SIZE (0)       // Not used - static allocation only

   #define configSUPPORT_STATIC_ALLOCATION (1)
   #define configSUPPORT_DYNAMIC_ALLOCATION (0)  // GC-first strategy

   // BLE SoftDevice coexistence
   #define configMAX_PRIORITIES (4)
   #define configMAX_TASK_NAME_LEN (8)

   // SoftDevice uses SVC and PendSV, must coordinate priorities
   #define configKERNEL_INTERRUPT_PRIORITY (7 << 5)  // Lowest priority
   #define configMAX_SYSCALL_INTERRUPT_PRIORITY (4 << 5)
   ```

3. **Update `ports/nrf/Makefile`:**
   ```makefile
   # Only enable threading for nRF52840 boards by default
   ifeq ($(MCU_SUB_VARIANT),nrf52840)
   MICROPY_PY_THREAD = 1
   FREERTOS_DIR = $(TOP)/lib/FreeRTOS-Kernel

   include $(TOP)/extmod/freertos/freertos.mk

   SRC_C += $(FREERTOS_DIR)/portable/GCC/ARM_CM4F/port.c
   CFLAGS_MOD += -I$(FREERTOS_DIR)/portable/GCC/ARM_CM4F
   endif
   ```

4. **Update `ports/nrf/mpconfigport.h`:**
   ```c
   #if defined(NRF52840)
   #define MICROPY_PY_THREAD (1)
   #define MICROPY_PY_THREAD_GIL (1)
   #define MICROPY_MPTHREADPORT_H "extmod/freertos/mpthreadport.h"
   #define mp_hal_delay_ms mp_freertos_delay_ms
   #endif
   ```

**nRF52832 (Optional, RAM-constrained):**

With only 64KB RAM, threading is marginal but possible:
```makefile
# In mpconfigboard.mk for specific board
MICROPY_PY_THREAD = 1
MP_FREERTOS_DEFAULT_STACK_SIZE = 2048  # Minimal stack
MP_FREERTOS_MAX_THREADS = 2            # Limit thread count
```

**nRF51 (Not Recommended):**

With 16-32KB RAM, threading is impractical. Do not enable.

**SoftDevice BLE Coexistence:**

The nRF52 SoftDevice (BLE stack) uses SVC and PendSV interrupts. FreeRTOS must be configured to avoid conflicts:
- FreeRTOS PendSV priority must be lower than SoftDevice
- Use `sd_nvic_*` functions instead of direct NVIC access when SoftDevice is enabled
- Consider the service task framework for BLE event processing

### 10.6 QEMU ARM Integration

1. Use generic ARM Cortex-M port files (ARM_CM3 or ARM_CM4F)
2. Configure minimal FreeRTOSConfig.h for simulation
3. Useful for CI/CD testing of threading functionality without hardware

### 10.7 FreeRTOS POSIX Simulator (Development/Testing)

FreeRTOS provides a POSIX/Linux simulator port that can run on Unix systems. While **NOT suitable for production Unix builds** (pthreads is the correct choice there), the simulator is useful for:

1. **Developing the FreeRTOS backend** without hardware
2. **Running unit tests** in CI pipelines
3. **Debugging threading issues** with host debuggers

**Setup:**
```bash
# In a test configuration, not the main Unix port
FREERTOS_DIR = $(TOP)/lib/FreeRTOS-Kernel
FREERTOS_PORT_DIR = $(FREERTOS_DIR)/portable/ThirdParty/GCC/Posix

SRC_C += $(FREERTOS_PORT_DIR)/port.c
SRC_C += $(FREERTOS_PORT_DIR)/utils/wait_for_event.c
CFLAGS += -I$(FREERTOS_PORT_DIR)
LDFLAGS += -pthread
```

**Use Cases:**
- Validate `mp_thread_gc_others()` stack scanning logic
- Test reaper mechanism without hardware resets
- Profile memory usage patterns
- Debug race conditions with AddressSanitizer/ThreadSanitizer

**Limitations:**
- Timing behavior differs from real hardware
- No true preemption (simulated via POSIX signals)
- Not representative of embedded memory constraints

---

## 11. Testing Requirements

### 11.1 Existing Test Suite

The following tests from `tests/thread/` MUST pass:

| Test File | Category | What It Tests |
|-----------|----------|---------------|
| `thread_start1.py`, `thread_start2.py` | Creation | Basic thread creation and execution |
| `thread_lock1.py` - `thread_lock5.py` | Synchronization | Mutex acquire/release, contention |
| `thread_ident1.py` | Identity | `_thread.get_ident()` uniqueness |
| `thread_exit1.py`, `thread_exit2.py` | Lifecycle | Thread termination |
| `thread_exc1.py`, `thread_exc2.py` | Exceptions | Exception handling in threads |
| `thread_gc1.py` | GC | Stack scanning during collection |
| `thread_shared1.py`, `thread_shared2.py` | Concurrency | Shared object access |
| `thread_sleep1.py`, `thread_sleep2.py` | Timing | `time.sleep()` behavior |
| `thread_stacksize1.py` | Memory | Custom stack sizes |
| `thread_heap_lock.py` | GC | Heap lock during allocation |
| `stress_create.py` | Stress | Rapid thread creation/destruction |
| `stress_heap.py` | Stress | Memory allocation under threading |
| `stress_recurse.py` | Stress | Deep recursion in threads |
| `mutate_*.py` | Safety | Concurrent mutation of data structures |

### 11.2 Test Execution

```bash
# Run on hardware target
cd ports/stm32
make BOARD=PYBV11 MICROPY_PY_THREAD=1
mpremote run ../../tests/thread/thread_start1.py

# Run full thread test suite
cd tests
./run-tests.py --target pyboard thread/
```

### 11.3 New Stress Test: `stress_freertos_gc.py`

```python
"""
FreeRTOS threading backend stress test.
Validates GC stack scanning and lazy cleanup.
"""
import _thread
import time
import gc

lock = _thread.allocate_lock()
counter = 0
errors = []

def worker(iteration):
    global counter, errors
    try:
        # Allocate objects that must survive GC
        local_list = [str(i) * 10 for i in range(100)]
        local_dict = {f"key{i}": i * 2 for i in range(50)}

        # Sleep to allow GC from other threads
        time.sleep(0.02)

        # Validate objects not corrupted by GC
        if len(local_list) != 100 or sum(len(s) for s in local_list) != 2970:
            errors.append(f"List corruption at iteration {iteration}")
        if len(local_dict) != 50:
            errors.append(f"Dict corruption at iteration {iteration}")

        with lock:
            counter += 1
    except Exception as e:
        errors.append(f"Exception at iteration {iteration}: {e}")

def run_stress(iterations=500, concurrent=4):
    global counter, errors
    counter = 0
    errors = []

    print(f"Starting stress test: {iterations} iterations, {concurrent} concurrent threads")
    start_mem = gc.mem_free()

    for i in range(iterations):
        # Start batch of threads
        for _ in range(concurrent):
            _thread.start_new_thread(worker, (i,))

        # Aggressive GC to catch issues
        gc.collect()

        # Brief delay between batches
        time.sleep(0.01)

        if i % 50 == 0:
            current_mem = gc.mem_free()
            print(f"Iter {i}: counter={counter}, mem_free={current_mem}, delta={start_mem - current_mem}")

    # Wait for stragglers
    time.sleep(2)
    gc.collect()

    print(f"\\nFinal: counter={counter} (expected ~{iterations * concurrent})")
    print(f"Memory: start={start_mem}, end={gc.mem_free()}")

    if errors:
        print(f"ERRORS ({len(errors)}):")
        for e in errors[:10]:
            print(f"  {e}")
        return False

    # Check for memory leak (allow some variance)
    if start_mem - gc.mem_free() > 8192:
        print("WARNING: Potential memory leak detected")
        return False

    print("PASS: All checks passed")
    return True

if __name__ == "__main__":
    run_stress()
```

### 11.4 Pass Criteria

1. **No Hard Faults** - No crashes, no SOS LED patterns
2. **All tests pass** - Output matches expected `.exp` files
3. **No memory leaks** - `gc.mem_free()` stabilizes over time
4. **Thread counter accurate** - All threads complete and increment counter
5. **No GC corruption** - Objects on thread stacks survive collection

---

## 12. Configuration Options

> **Note:** For the full configuration cascade architecture, see Section 2.3.

### 12.1 MicroPython Core Threading Macros (py/mpconfig.h)

These are defined in the core MicroPython config and can be overridden at port/board level:

| Macro | Default | Description |
|-------|---------|-------------|
| `MICROPY_PY_THREAD` | `0` | Master enable for threading module |
| `MICROPY_PY_THREAD_GIL` | `MICROPY_PY_THREAD` | Enable Global Interpreter Lock |
| `MICROPY_PY_THREAD_GIL_VM_DIVISOR` | `32` | VM instructions between GIL release checks |
| `MICROPY_PY_THREAD_RECURSIVE_MUTEX` | `(MICROPY_PY_THREAD && !GIL)` | Enable recursive mutex support |

### 12.2 Backend Selection Macros (mpconfigport.h)

| Macro | Default | Description |
|-------|---------|-------------|
| `MICROPY_MPTHREADPORT_H` | (none) | Path to threading backend header |
| `MICROPY_MPHALPORT_H` | (none) | Path to HAL port header |

### 12.3 FreeRTOS Backend Macros (extmod/freertos)

These are specific to the FreeRTOS backend implementation:

| Macro | Default | Description |
|-------|---------|-------------|
| `MP_FREERTOS_TLS_INDEX` | `0` | Thread-local storage index for mp_state |
| `MP_FREERTOS_THREAD_PRIORITY` | `tskIDLE_PRIORITY + 1` | Default thread priority |
| `MP_FREERTOS_MIN_STACK_SIZE` | `2048` | Minimum stack size in bytes |
| `MP_FREERTOS_DEFAULT_STACK_SIZE` | `4096` | Default stack size in bytes |
| `MP_FREERTOS_IDLE_THREAD_STACKSIZE` | `configMINIMAL_STACK_SIZE` | Idle thread stack (if used) |

### 12.4 HAL Integration Macros (mphalport.h)

| Macro | Purpose | FreeRTOS Implementation |
|-------|---------|-------------------------|
| `MICROPY_BEGIN_ATOMIC_SECTION()` | Enter critical section | `taskENTER_CRITICAL()` or `disable_irq()` |
| `MICROPY_END_ATOMIC_SECTION(s)` | Exit critical section | `taskEXIT_CRITICAL()` or `enable_irq(s)` |
| `mp_hal_delay_ms` | Millisecond delay | Can redirect to `mp_freertos_delay_ms` |
| `MICROPY_EVENT_POLL_HOOK` | Background processing | GIL-aware yield |

### 12.5 FreeRTOSConfig.h Requirements

See Section 5 for full requirements. Key settings:

| Macro | Required Value | Purpose |
|-------|----------------|---------|
| `configSUPPORT_STATIC_ALLOCATION` | `1` | **Mandatory** - enables `xTaskCreateStatic()` |
| `configNUM_THREAD_LOCAL_STORAGE_POINTERS` | `≥ 1` | Thread state storage |
| `configUSE_MUTEXES` | `1` | GIL and lock support |
| `configUSE_RECURSIVE_MUTEXES` | `1` | Recursive lock support |
| `INCLUDE_vTaskDelete` | `1` | `_thread.exit()` support |
| `INCLUDE_xTaskGetCurrentTaskHandle` | `1` | Main thread adoption |

### 12.6 Runtime Behavior

| Feature | Behavior |
|---------|----------|
| `_thread.stack_size()` | Returns/sets default stack size |
| `_thread.start_new_thread(func, args, kwargs, stacksize)` | Custom stack per thread |
| `_thread.get_ident()` | Returns FreeRTOS TaskHandle_t as integer |
| `_thread.allocate_lock()` | Creates FreeRTOS binary semaphore |
| `_thread.exit()` | Marks thread FINISHED, yields forever |

### 12.7 Service Task Framework Macros (optional)

See Section 13.8 for full framework documentation.

| Macro | Default | Description |
|-------|---------|-------------|
| `MICROPY_FREERTOS_SERVICE_TASKS` | `1` | Enable service task framework |
| `MICROPY_FREERTOS_MAX_SERVICES` | `8` | Maximum registered services |
| `MICROPY_FREERTOS_SERVICE_STATIC_ALLOC` | `1` | Use static allocation for service tasks |
| `MP_FREERTOS_PRIO_PYTHON` | `configMAX_PRIORITIES - 4` | MicroPython interpreter priority |
| `MP_FREERTOS_PRIO_USB` | `configMAX_PRIORITIES - 3` | TinyUSB task priority |
| `MP_FREERTOS_PRIO_NETWORK` | `configMAX_PRIORITIES - 3` | lwIP/WiFi/BLE task priority |
| `MP_FREERTOS_SERVICE_STACK_USB` | `4096` | USB service stack size |
| `MP_FREERTOS_SERVICE_STACK_NET` | `4096` | Network service stack size |

---

## 13. SMP and Multi-Threading Considerations

### 13.1 Multi-Core Support (RP2040, ESP32)

For SMP targets:
1. Use `FreeRTOS-Kernel-SMP` or ESP-IDF's SMP variant
2. Configure `configNUMBER_OF_CORES`
3. Consider `xTaskCreateAffinitySet()` for core pinning
4. GC MUST be atomic across all cores (use `portENTER_CRITICAL()`)

### 13.2 Core Affinity Hook

```c
// Optional: Allow specifying core affinity
#ifdef configUSE_CORE_AFFINITY
mp_uint_t mp_thread_create_ex(void *(*entry)(void *), void *arg,
                               size_t *stack_size, UBaseType_t core_affinity);
#endif
```

### 13.3 RP2 Enhanced Threading Motivation

The current RP2 port is limited to 2 threads (one per core). FreeRTOS enables:

1. **Multiple Python threads** - Not limited by core count
2. **Background service tasks** - BLE, WiFi, lwIP, USB as independent tasks
3. **Priority-based preemption** - Services can interrupt Python when needed

### 13.4 Background Service Task Architecture

Reference architecture from [micropythonrt](https://github.com/gneverov/micropythonrt):

```
┌──────────────────────────────────────────────────────────┐
│                    FreeRTOS Scheduler                     │
├──────────────────────────────────────────────────────────┤
│  Priority 3: init_task (startup, spawns other tasks)     │
├──────────────────────────────────────────────────────────┤
│  Priority 2: tud_task (TinyUSB device)                   │
│  Priority 2: tuh_task (TinyUSB host)                     │
│  Priority 2: lwip_task (TCP/IP stack)                    │
│  Priority 2: cyw43_task (WiFi/BLE driver)                │
├──────────────────────────────────────────────────────────┤
│  Priority 1: mp_task (MicroPython interpreter + threads) │
└──────────────────────────────────────────────────────────┘
```

**Key Design Principles:**
- MicroPython runs at **lowest priority** - can be preempted by services
- Service tasks run at **higher priority** - responsive to hardware events
- All Python threads share `mp_task` priority or lower
- Service tasks do NOT hold GIL - they're pure C

### 13.5 Task Initialization Pattern

```c
// main.c - FreeRTOS task startup for RP2
void main(void) {
    // Create init task at high priority
    xTaskCreate(init_task, "init", INIT_STACK_SIZE / sizeof(StackType_t),
                NULL, 3, NULL);
    vTaskStartScheduler();  // Never returns
}

static void init_task(void *arg) {
    // Initialize hardware
    flash_lockout_init();

    // Start networking (lwIP runs as its own task internally)
    #if MICROPY_PY_LWIP
    lwip_helper_init();
    #endif

    // Start USB device task
    #if MICROPY_HW_ENABLE_USBDEV
    tud_init(0);
    xTaskCreate(tud_task, "tud", TUD_STACK_SIZE / sizeof(StackType_t),
                NULL, 2, NULL);
    #endif

    // Start MicroPython task (lower priority)
    xTaskCreate(mp_task, "mp", MP_TASK_STACK_SIZE / sizeof(StackType_t),
                NULL, 1, NULL);

    // Delete init task (its job is done)
    vTaskDelete(NULL);
}

static void mp_task(void *arg) {
    mp_thread_init(...);  // Register as main thread
    // ... MicroPython initialization ...
    // ... REPL loop ...
}
```

### 13.6 Service Task Integration Points

| Service | Task Name | Priority | Stack | Notes |
|---------|-----------|----------|-------|-------|
| TinyUSB Device | `tud` | 2 | 4KB | USB CDC, MSC, HID |
| TinyUSB Host | `tuh` | 2 | 4KB | USB host mode |
| lwIP | (internal) | 2 | 4KB | TCP/IP stack processing |
| CYW43 WiFi | `cyw43` | 2 | 4KB | WiFi/BLE event handling |
| MicroPython | `mp` | 1 | 16KB+ | Interpreter + Python threads |

### 13.7 GIL Interaction with Service Tasks

Service tasks do NOT acquire the GIL. They communicate with Python via:

1. **Callbacks/IRQs** - Schedule Python callback via `mp_sched_schedule()`
2. **Shared buffers** - Protected by FreeRTOS mutexes (not GIL)
3. **Event flags** - FreeRTOS event groups or task notifications

```c
// Example: USB task notifying Python
static void tud_task(void *arg) {
    while (1) {
        tud_task_handler();  // Process USB events

        if (data_ready) {
            // Schedule Python callback (thread-safe)
            mp_sched_schedule(callback_obj, mp_const_none);
        }

        vTaskDelay(1);  // Yield to lower priority tasks
    }
}
```

### 13.8 Generalized Service Task Framework

The background service task pattern should be a **common optional framework** in `extmod/freertos/` that any port can use, not just RP2.

#### 13.8.1 Applicable Ports

| Port | Services That Benefit |
|------|----------------------|
| **RP2** | TinyUSB, lwIP, CYW43 (WiFi/BLE), PIO |
| **STM32** | TinyUSB, lwIP, ETH MAC, BLE (WB55/WL55) |
| **mimxrt** | TinyUSB, lwIP, ETH MAC |
| **samd** | TinyUSB, WiFi (WINC1500) |
| **nRF** | SoftDevice BLE, TinyUSB (nRF52840), lwIP |
| **ESP32** | Already handled by ESP-IDF internally |

#### 13.8.2 Standard Priority Levels

```c
// extmod/freertos/mp_freertos_service.h

// Standard priority levels (relative to configMAX_PRIORITIES)
#define MP_FREERTOS_PRIO_INIT       (configMAX_PRIORITIES - 1)  // Highest: startup
#define MP_FREERTOS_PRIO_ISR_DEFER  (configMAX_PRIORITIES - 2)  // Deferred ISR processing
#define MP_FREERTOS_PRIO_NETWORK    (configMAX_PRIORITIES - 3)  // lwIP, WiFi, BLE
#define MP_FREERTOS_PRIO_USB        (configMAX_PRIORITIES - 3)  // TinyUSB (same as network)
#define MP_FREERTOS_PRIO_PYTHON     (configMAX_PRIORITIES - 4)  // MicroPython interpreter
#define MP_FREERTOS_PRIO_BACKGROUND (configMAX_PRIORITIES - 5)  // Low-priority background

// Default stack sizes
#define MP_FREERTOS_SERVICE_STACK_MIN    (2048)
#define MP_FREERTOS_SERVICE_STACK_USB    (4096)
#define MP_FREERTOS_SERVICE_STACK_NET    (4096)
#define MP_FREERTOS_SERVICE_STACK_PYTHON (16384)
```

#### 13.8.3 Service Task API

```c
// extmod/freertos/mp_freertos_service.h

typedef struct _mp_freertos_service_t {
    const char *name;                    // Task name (for debugging)
    TaskFunction_t entry;                // Task entry point
    uint16_t stack_size;                 // Stack size in bytes
    uint8_t priority;                    // FreeRTOS priority
    uint8_t flags;                       // See MP_SERVICE_FLAG_*
    TaskHandle_t handle;                 // Filled by framework
    StaticTask_t *tcb;                   // For static allocation
    StackType_t *stack;                  // For static allocation
} mp_freertos_service_t;

// Service flags
#define MP_SERVICE_FLAG_STATIC      (1 << 0)  // Use static allocation (GC-safe)
#define MP_SERVICE_FLAG_AUTOSTART   (1 << 1)  // Start automatically at init
#define MP_SERVICE_FLAG_ESSENTIAL   (1 << 2)  // Don't stop on soft reset

// Service lifecycle
void mp_freertos_service_init(void);                           // Initialize framework
void mp_freertos_service_register(mp_freertos_service_t *svc); // Register a service
void mp_freertos_service_start(mp_freertos_service_t *svc);    // Start service task
void mp_freertos_service_stop(mp_freertos_service_t *svc);     // Stop service task
void mp_freertos_service_stop_all(void);                       // Stop non-essential services
void mp_freertos_service_deinit(void);                         // Cleanup on soft reset
```

#### 13.8.4 Service Registration Pattern

```c
// Port-specific: ports/rp2/mpserviceport.c

#include "extmod/freertos/mp_freertos_service.h"

// Define services
static mp_freertos_service_t tinyusb_service = {
    .name = "tud",
    .entry = tud_task,
    .stack_size = MP_FREERTOS_SERVICE_STACK_USB,
    .priority = MP_FREERTOS_PRIO_USB,
    .flags = MP_SERVICE_FLAG_STATIC | MP_SERVICE_FLAG_AUTOSTART | MP_SERVICE_FLAG_ESSENTIAL,
};

#if MICROPY_PY_LWIP
static mp_freertos_service_t lwip_service = {
    .name = "lwip",
    .entry = lwip_task,
    .stack_size = MP_FREERTOS_SERVICE_STACK_NET,
    .priority = MP_FREERTOS_PRIO_NETWORK,
    .flags = MP_SERVICE_FLAG_STATIC | MP_SERVICE_FLAG_AUTOSTART,
};
#endif

// Called during port initialization
void mp_freertos_port_services_init(void) {
    mp_freertos_service_register(&tinyusb_service);
    #if MICROPY_PY_LWIP
    mp_freertos_service_register(&lwip_service);
    #endif
}
```

#### 13.8.5 Soft Reset Handling

Services marked `MP_SERVICE_FLAG_ESSENTIAL` (like USB CDC for REPL) survive soft reset. Others are stopped:

```c
// Called from mp_deinit() or soft reset path
void mp_freertos_handle_soft_reset(void) {
    // Stop non-essential services
    mp_freertos_service_stop_all();

    // Wait for tasks to terminate
    vTaskDelay(pdMS_TO_TICKS(100));

    // Essential services continue running (USB for REPL)
}
```

#### 13.8.6 Service-to-Python Communication

Standard patterns for services to communicate with Python:

| Pattern | Use Case | API |
|---------|----------|-----|
| **Scheduled callback** | Event notification | `mp_sched_schedule(callback, arg)` |
| **Stream buffer** | Data transfer (USB, UART) | `xStreamBufferSend()` / `xStreamBufferReceive()` |
| **Event group** | State flags | `xEventGroupSetBits()` / `xEventGroupWaitBits()` |
| **Queue** | Command/response | `xQueueSend()` / `xQueueReceive()` |

```c
// Example: Service notifying Python of incoming data
void service_notify_data_ready(mp_obj_t callback) {
    // Thread-safe: can be called from any task
    if (callback != mp_const_none) {
        mp_sched_schedule(callback, mp_const_none);
    }
}
```

#### 13.8.7 Directory Structure

```
extmod/freertos/
├── mpthreadport.h          # Threading API (existing)
├── mpthreadport.c          # Threading implementation (existing)
├── mp_freertos_service.h   # Service task framework API
├── mp_freertos_service.c   # Service task framework implementation
├── mp_freertos_hal.h       # HAL helpers (delay, critical sections)
├── mp_freertos_hal.c       # HAL implementation
├── freertos.mk             # Makefile fragment
└── freertos.cmake          # CMake fragment

ports/<port>/
├── mpserviceport.c         # Port-specific service registration
└── FreeRTOSConfig.h        # Port-specific FreeRTOS config
```

#### 13.8.8 Configuration Macros

```c
// Enable/disable service framework (default: enabled when FREERTOS backend used)
#ifndef MICROPY_FREERTOS_SERVICE_TASKS
#define MICROPY_FREERTOS_SERVICE_TASKS (1)
#endif

// Maximum number of registered services
#ifndef MICROPY_FREERTOS_MAX_SERVICES
#define MICROPY_FREERTOS_MAX_SERVICES (8)
#endif

// Use static allocation for service tasks (recommended)
#ifndef MICROPY_FREERTOS_SERVICE_STATIC_ALLOC
#define MICROPY_FREERTOS_SERVICE_STATIC_ALLOC (1)
#endif
```

---

## 14. Error Handling

### 14.1 Thread Creation Failures

```c
mp_uint_t mp_thread_create(...) {
    // ...allocation...

    if (tcb == NULL || stack == NULL || th == NULL) {
        // Allocation failed - clean up partial allocations
        m_del_obj_maybe(StaticTask_t, tcb);
        m_del_obj_maybe(StackType_t, stack);
        m_del_obj_maybe(mp_thread_t, th);
        mp_raise_msg(&mp_type_MemoryError, MP_ERROR_TEXT("can't allocate thread"));
    }

    th->id = xTaskCreateStatic(...);
    if (th->id == NULL) {
        m_del(StaticTask_t, tcb, 1);
        m_del(StackType_t, stack, stack_len);
        m_del_obj(mp_thread_t, th);
        mp_raise_msg(&mp_type_OSError, MP_ERROR_TEXT("can't create thread"));
    }

    // ...
}
```

### 14.2 Stack Overflow Detection

If `configCHECK_FOR_STACK_OVERFLOW` is enabled:
```c
void vApplicationStackOverflowHook(TaskHandle_t xTask, char *pcTaskName) {
    mp_printf(&mp_plat_print, "Stack overflow in task: %s\n", pcTaskName);
    MICROPY_BOARD_FATAL_ERROR("stack overflow");
}
```

---

## 15. Migration Path for Existing Ports

### 15.1 ESP32 Migration (Optional)

The ESP32 port already works. Migration to common backend:
1. Keep `xTaskCreatePinnedToCore` via `MP_THREAD_CREATE_HOOK` macro
2. Preserve ESP-IDF FreeRTOS configuration
3. Benefit: Unified codebase, shared bug fixes

### 15.2 STM32 Migration (Required)

Current `pybthread.c` custom scheduler limitations:
- Cooperative only (no preemption)
- Custom mutex implementation
- No priority support

Benefits of FreeRTOS:
- Preemptive scheduling
- Priority inheritance for mutexes
- Better tooling support (debugging, tracing)
- Standard RTOS semantics

### 15.3 Backward Compatibility

The `MICROPY_PY_THREAD` macro should continue to work for ports that:
1. Don't want FreeRTOS
2. Have their own threading backend

Add new macro: `MICROPY_PY_THREAD_FREERTOS` for explicit selection.

---

## 16. References

### 16.1 Source Documents

1. **gem-freertos.md** - Original feature discussion transcript
2. **zephyr-threading-memory-architecture.md** - Memory model reference implementation
3. **plan_freertos_backend.md** - Previous iteration planning document

### 16.2 External References

1. FreeRTOS Documentation: https://www.freertos.org/Documentation
2. MicroPython Threading: https://docs.micropython.org/en/latest/library/_thread.html
3. ARM AAPCS: https://developer.arm.com/documentation/ihi0042/latest

### 16.3 Relevant Codebase Files

| File | Purpose |
|------|---------|
| `py/mpthread.h` | Generic threading API |
| `py/modthread.c` | `_thread` module implementation |
| `py/mpstate.h` | Thread-local state definitions |
| `ports/esp32/mpthreadport.c` | Reference FreeRTOS implementation |
| `ports/stm32/pybthread.c` | Current STM32 custom scheduler (to be replaced) |
| `ports/rp2/mpthreadport.c` | Current RP2 multicore implementation (to be replaced) |
| `ports/unix/mpthreadport.c` | POSIX pthreads reference (NOT FreeRTOS target) |
| `ports/mimxrt/mpconfigport.h` | mimxrt configuration (no threading currently) |
| `ports/samd/mpconfigport.h` | SAMD configuration (no threading currently) |
| `ports/nrf/mpconfigport.h` | nRF configuration (no threading currently) |

### 16.4 FreeRTOS Kernel Ports by Architecture

| MicroPython Port | FreeRTOS Portable Directory |
|------------------|----------------------------|
| STM32 F4/F7/H7 | `portable/GCC/ARM_CM4F` or `ARM_CM7/r0p1` |
| RP2 | `portable/GCC/ARM_CM0` (SMP variant) |
| mimxrt | `portable/GCC/ARM_CM7/r0p1` |
| SAMD21 | `portable/GCC/ARM_CM0` |
| SAMD51 | `portable/GCC/ARM_CM4F` |
| nRF51 | `portable/GCC/ARM_CM0` |
| nRF52832/52840 | `portable/GCC/ARM_CM4F` |
| nRF9160 | `portable/GCC/ARM_CM33_NTZ/non_secure` |
| QEMU (generic) | `portable/GCC/ARM_CM3` or `ARM_CM4F` |
| POSIX Simulator | `portable/ThirdParty/GCC/Posix` |

---

## 17. Acceptance Criteria

The implementation is complete when:

### 17.1 Core Implementation
1. [ ] `extmod/freertos/` directory contains all specified files
2. [ ] Code follows MicroPython style (codeformat.py passes)
3. [ ] Build system fragments work for Make and CMake ports
4. [ ] GC correctly scans all thread stacks
5. [ ] Thread cleanup (reaper) works correctly
6. [ ] Memory does not leak over extended operation

### 17.2 Primary Ports (Required)
7. [ ] STM32 port builds and passes all `tests/thread/*.py` tests
8. [ ] RP2 port builds and passes all `tests/thread/*.py` tests

### 17.3 Secondary Ports (Target)
9. [ ] mimxrt port builds with `MICROPY_PY_THREAD=1`
10. [ ] SAMD51 builds with `MICROPY_PY_THREAD=1`
11. [ ] nRF52840 builds with `MICROPY_PY_THREAD=1`

### 17.4 Testing Infrastructure
12. [ ] QEMU ARM configuration available for CI testing
13. [ ] FreeRTOS POSIX simulator build configuration documented
14. [ ] New stress test `stress_freertos_gc.py` passes on all ports

### 17.5 Documentation
15. [ ] Integration guide updated for all target ports
16. [ ] FreeRTOSConfig.h template documented with required settings
