/*
 * This file is part of the MicroPython project, http://micropython.org/
 *
 * The MIT License (MIT)
 *
 * Copyright (c) 2025 MicroPython Contributors
 *
 * Permission is hereby granted, free of charge, to any person obtaining a copy
 * of this software and associated documentation files (the "Software"), to deal
 * in the Software without restriction, including without limitation the rights
 * to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
 * copies of the Software, and to permit persons to whom the Software is
 * furnished to do so, subject to the following conditions:
 *
 * The above copyright notice and this permission notice shall be included in
 * all copies or substantial portions of the Software.
 *
 * THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
 * IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
 * FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
 * AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
 * LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
 * OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
 * THE SOFTWARE.
 */
#ifndef MICROPY_INCLUDED_EXTMOD_FREERTOS_MPTHREADPORT_H
#define MICROPY_INCLUDED_EXTMOD_FREERTOS_MPTHREADPORT_H

#include "py/mpconfig.h"

#if MICROPY_PY_THREAD

// FreeRTOS headers only available after CMake target setup (not during qstr extraction)
#ifndef NO_QSTR
#include "FreeRTOS.h"
#include "task.h"
#include "semphr.h"
#else
// Stub definitions for qstr extraction phase (types only, macros not needed)
typedef void *TaskHandle_t;
typedef struct { char _[256]; } StaticTask_t;
typedef struct { char _[96]; } StaticSemaphore_t;
typedef void *SemaphoreHandle_t;
#endif

// Thread-local storage index for mp_state_thread_t pointer
#ifndef MP_FREERTOS_TLS_INDEX
#define MP_FREERTOS_TLS_INDEX (0)
#endif

// Default GIL enabled for safety on single-core systems.
// Multi-core ports (e.g., rp2) can override to 0 for true parallelism.
#ifndef MICROPY_PY_THREAD_GIL
#define MICROPY_PY_THREAD_GIL (1)
#endif

// Default stack alignment (8-byte for ARM AAPCS)
#ifndef MP_THREAD_STACK_ALIGN
#define MP_THREAD_STACK_ALIGN (8)
#endif

// Default thread priority (above idle, below drivers)
#ifndef MP_THREAD_PRIORITY
#define MP_THREAD_PRIORITY (tskIDLE_PRIORITY + 1)
#endif

// Core affinity mask for SMP systems.
// With GIL disabled and spinlock-based atomic sections, threads can safely run
// on any core. tskNO_AFFINITY allows FreeRTOS to schedule on any available core.
// Ports can override to pin to specific core(s) if needed.
#ifndef MP_THREAD_CORE_AFFINITY
#define MP_THREAD_CORE_AFFINITY tskNO_AFFINITY  // Allow any core
#endif

// Default stack size for Python threads (in bytes)
#ifndef MP_THREAD_DEFAULT_STACK_SIZE
#define MP_THREAD_DEFAULT_STACK_SIZE (4096)
#endif

// Minimum stack size for Python threads (in bytes)
#ifndef MP_THREAD_MIN_STACK_SIZE
#define MP_THREAD_MIN_STACK_SIZE (2048)
#endif

// Thread state enumeration
typedef enum {
    MP_THREAD_STATE_NEW = 0,
    MP_THREAD_STATE_RUNNING,
    MP_THREAD_STATE_FINISHED,
} mp_thread_state_t;

// Thread structure - all memory GC-allocated
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

// Mutex structure using static allocation
typedef struct _mp_thread_mutex_t {
    StaticSemaphore_t static_sem; // Static storage for semaphore
    SemaphoreHandle_t handle;     // FreeRTOS handle
} mp_thread_mutex_t;

// Recursive mutex structure
typedef struct _mp_thread_recursive_mutex_t {
    StaticSemaphore_t static_sem; // Static storage for semaphore
    SemaphoreHandle_t handle;     // FreeRTOS handle
} mp_thread_recursive_mutex_t;

// Thread lifecycle functions (called from port main.c)
// stack/stack_len describe main thread's stack (NULL if handled separately)
void mp_thread_init(void *stack, uint32_t stack_len);
void mp_thread_deinit(void);

// GC integration (called from gc.c during garbage collection)
void mp_thread_gc_others(void);

#endif // MICROPY_PY_THREAD

#endif // MICROPY_INCLUDED_EXTMOD_FREERTOS_MPTHREADPORT_H
