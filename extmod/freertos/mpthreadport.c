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

#include "py/mpconfig.h"

#if MICROPY_PY_THREAD

#include "py/runtime.h"
#include "py/gc.h"
#include "py/mpstate.h"
#include "py/mpthread.h"
#include "py/mperrno.h"
#include "extmod/freertos/mpthreadport.h"

// ============================================================================
// Phase 4: Thread State Management
// ============================================================================

// Get the thread-local MicroPython state for the current thread.
// Uses FreeRTOS Thread Local Storage (TLS) at index MP_FREERTOS_TLS_INDEX.
struct _mp_state_thread_t *mp_thread_get_state(void) {
    return (struct _mp_state_thread_t *)pvTaskGetThreadLocalStoragePointer(NULL, MP_FREERTOS_TLS_INDEX);
}

// Set the thread-local MicroPython state for the current thread.
void mp_thread_set_state(struct _mp_state_thread_t *state) {
    vTaskSetThreadLocalStoragePointer(NULL, MP_FREERTOS_TLS_INDEX, state);
}

// Get a unique identifier for the current thread (FreeRTOS task handle).
mp_uint_t mp_thread_get_id(void) {
    return (mp_uint_t)xTaskGetCurrentTaskHandle();
}

// ============================================================================
// Phase 5: Mutex Implementation
// ============================================================================

// Initialize a mutex using static allocation.
// Uses a binary semaphore given initially (unlocked state).
void mp_thread_mutex_init(mp_thread_mutex_t *mutex) {
    mutex->handle = xSemaphoreCreateBinaryStatic(&mutex->static_sem);
    // Binary semaphore starts "empty" (locked), so give it to make it available
    xSemaphoreGive(mutex->handle);
}

// Lock a mutex.
// wait: 1 = block until acquired, 0 = try without blocking
// Returns: 1 = acquired, 0 = not acquired (only possible if wait=0)
int mp_thread_mutex_lock(mp_thread_mutex_t *mutex, int wait) {
    // Before scheduler starts, locks always succeed (single-threaded)
    if (xTaskGetSchedulerState() != taskSCHEDULER_RUNNING) {
        return 1;
    }

    TickType_t timeout = wait ? portMAX_DELAY : 0;
    return xSemaphoreTake(mutex->handle, timeout) == pdTRUE ? 1 : 0;
}

// Unlock a mutex.
// Yields after unlocking to give other threads a chance to acquire it.
// This is important for GIL fairness - without yielding, the releasing
// thread could immediately re-acquire the mutex before others run.
void mp_thread_mutex_unlock(mp_thread_mutex_t *mutex) {
    // Before scheduler starts, nothing to do
    if (xTaskGetSchedulerState() != taskSCHEDULER_RUNNING) {
        return;
    }

    xSemaphoreGive(mutex->handle);
    taskYIELD();
}

#if MICROPY_PY_THREAD_RECURSIVE_MUTEX

// Initialize a recursive mutex using static allocation.
void mp_thread_recursive_mutex_init(mp_thread_recursive_mutex_t *mutex) {
    mutex->handle = xSemaphoreCreateRecursiveMutexStatic(&mutex->static_sem);
}

// Lock a recursive mutex.
// Same thread can acquire multiple times; must unlock same number of times.
int mp_thread_recursive_mutex_lock(mp_thread_recursive_mutex_t *mutex, int wait) {
    // Before scheduler starts, locks always succeed (single-threaded)
    if (xTaskGetSchedulerState() != taskSCHEDULER_RUNNING) {
        return 1;
    }

    TickType_t timeout = wait ? portMAX_DELAY : 0;
    return xSemaphoreTakeRecursive(mutex->handle, timeout) == pdTRUE ? 1 : 0;
}

// Unlock a recursive mutex.
void mp_thread_recursive_mutex_unlock(mp_thread_recursive_mutex_t *mutex) {
    // Before scheduler starts, nothing to do
    if (xTaskGetSchedulerState() != taskSCHEDULER_RUNNING) {
        return;
    }

    xSemaphoreGiveRecursive(mutex->handle);
}

#endif // MICROPY_PY_THREAD_RECURSIVE_MUTEX

// ============================================================================
// Phase 6: Thread Lifecycle
// ============================================================================

// Global state for thread management
static mp_thread_mutex_t thread_mutex;
static mp_thread_t main_thread;

// Register thread list as GC root so thread structures are not collected
// Accessed via MP_STATE_VM(mp_thread_list_head)
MP_REGISTER_ROOT_POINTER(struct _mp_thread_t *mp_thread_list_head);

// Forward declarations
static void freertos_entry_wrapper(void *arg);
static void mp_thread_reap_dead_threads(void);

// Initialize the threading subsystem and adopt the main thread.
// Called from port's main() after GC init.
// stack/stack_len describe main thread's stack (can be NULL if handled elsewhere).
void mp_thread_init(void *stack, uint32_t stack_len) {
    mp_thread_mutex_init(&thread_mutex);

    // Set up main thread entry
    main_thread.id = xTaskGetCurrentTaskHandle();
    main_thread.tcb = NULL;  // Not statically allocated by us
    main_thread.stack = stack;
    main_thread.stack_len = stack_len / sizeof(StackType_t);
    main_thread.arg = NULL;
    main_thread.entry = NULL;
    main_thread.state = MP_THREAD_STATE_RUNNING;
    main_thread.next = NULL;

    // Add main thread to list
    MP_STATE_VM(mp_thread_list_head) = &main_thread;

    // Set TLS for main thread
    mp_thread_set_state(&mp_state_ctx.thread);
}

// Deinitialize threading subsystem (called on soft reset).
void mp_thread_deinit(void) {
    mp_thread_mutex_lock(&thread_mutex, 1);

    // Clean up all threads except main
    mp_thread_t *th = MP_STATE_VM(mp_thread_list_head);
    while (th != NULL) {
        mp_thread_t *next = th->next;
        if (th != &main_thread) {
            // Delete the FreeRTOS task
            if (th->id != NULL) {
                vTaskDelete(th->id);
            }
            // Free GC-allocated memory
            if (th->stack != NULL) {
                m_del(StackType_t, th->stack, th->stack_len);
            }
            if (th->tcb != NULL) {
                m_del(StaticTask_t, th->tcb, 1);
            }
            m_del_obj(mp_thread_t, th);
        }
        th = next;
    }

    // Reset list to just main thread
    MP_STATE_VM(mp_thread_list_head) = &main_thread;
    main_thread.next = NULL;

    mp_thread_mutex_unlock(&thread_mutex);
}

// Entry wrapper for new threads - sets up TLS and handles exit.
static void freertos_entry_wrapper(void *arg) {
    mp_thread_t *th = (mp_thread_t *)arg;

    // Initialize thread-local state on this thread's stack
    mp_state_thread_t ts;
    mp_thread_set_state(&ts);

    // Signal thread is starting (GIL acquisition happens in modthread.c)
    mp_thread_start();

    // Execute the Python entry function
    th->entry(th->arg);

    // Mark thread as finished
    mp_thread_mutex_lock(&thread_mutex, 1);
    th->state = MP_THREAD_STATE_FINISHED;
    mp_thread_mutex_unlock(&thread_mutex);

    // Cannot return or free own stack - wait to be reaped
    for (;;) {
        vTaskDelay(portMAX_DELAY);
    }
}

// Clean up finished threads (lazy cleanup / reaper).
// Called from mp_thread_create() before creating new threads.
static void mp_thread_reap_dead_threads(void) {
    mp_thread_mutex_lock(&thread_mutex, 1);

    mp_thread_t **prev_ptr = &MP_STATE_VM(mp_thread_list_head);
    mp_thread_t *th = MP_STATE_VM(mp_thread_list_head);

    while (th != NULL) {
        if (th->state == MP_THREAD_STATE_FINISHED && th != &main_thread) {
            // Unlink from list
            *prev_ptr = th->next;
            mp_thread_t *to_free = th;
            th = th->next;

            // Delete FreeRTOS task
            if (to_free->id != NULL) {
                vTaskDelete(to_free->id);
            }

            // Free GC-allocated memory
            if (to_free->stack != NULL) {
                m_del(StackType_t, to_free->stack, to_free->stack_len);
            }
            if (to_free->tcb != NULL) {
                m_del(StaticTask_t, to_free->tcb, 1);
            }
            m_del_obj(mp_thread_t, to_free);
        } else {
            prev_ptr = &th->next;
            th = th->next;
        }
    }

    mp_thread_mutex_unlock(&thread_mutex);
}

// Create a new thread.
// entry: function to run in new thread
// arg: argument to pass to entry function
// stack_size: pointer to requested stack size (updated with actual size on return)
// Returns: thread ID on success
mp_uint_t mp_thread_create(void *(*entry)(void *), void *arg, size_t *stack_size) {
    // Clean up any finished threads first
    mp_thread_reap_dead_threads();

    // Ensure minimum stack size
    if (*stack_size == 0) {
        *stack_size = MP_THREAD_DEFAULT_STACK_SIZE;
    } else if (*stack_size < MP_THREAD_MIN_STACK_SIZE) {
        *stack_size = MP_THREAD_MIN_STACK_SIZE;
    }

    // Align stack size to StackType_t boundary
    size_t stack_len = (*stack_size + sizeof(StackType_t) - 1) / sizeof(StackType_t);
    *stack_size = stack_len * sizeof(StackType_t);

    // Allocate thread resources from GC heap
    mp_thread_t *th = m_new_obj(mp_thread_t);
    if (th == NULL) {
        goto fail_thread;
    }

    th->tcb = m_new(StaticTask_t, 1);
    if (th->tcb == NULL) {
        goto fail_tcb;
    }

    th->stack = m_new(StackType_t, stack_len);
    if (th->stack == NULL) {
        goto fail_stack;
    }

    // Initialize thread structure
    th->stack_len = stack_len;
    th->arg = arg;
    th->entry = entry;
    th->state = MP_THREAD_STATE_NEW;
    th->next = NULL;

    // Create the FreeRTOS task with static allocation
    th->id = xTaskCreateStatic(
        freertos_entry_wrapper,
        "MPThread",
        stack_len,
        th,
        MP_THREAD_PRIORITY,
        th->stack,
        th->tcb
        );

    if (th->id == NULL) {
        goto fail_task;
    }

    // Mark as running and add to thread list
    mp_thread_mutex_lock(&thread_mutex, 1);
    th->state = MP_THREAD_STATE_RUNNING;
    th->next = MP_STATE_VM(mp_thread_list_head);
    MP_STATE_VM(mp_thread_list_head) = th;
    mp_thread_mutex_unlock(&thread_mutex);

    return (mp_uint_t)th->id;

    // Cleanup on allocation failure
fail_task:
    m_del(StackType_t, th->stack, stack_len);
fail_stack:
    m_del(StaticTask_t, th->tcb, 1);
fail_tcb:
    m_del_obj(mp_thread_t, th);
fail_thread:
    mp_raise_OSError(MP_ENOMEM);
    return 0;  // Unreachable
}

// Called when a thread starts (from entry wrapper).
void mp_thread_start(void) {
    // Thread is already in list from mp_thread_create
    // This hook exists for GIL acquisition in modthread.c
}

// Called when a thread finishes (from modthread.c).
void mp_thread_finish(void) {
    // State is set to FINISHED in entry wrapper
    // Actual cleanup happens in reaper
}

// ============================================================================
// Phase 7: GC Integration
// ============================================================================

// Scan all thread stacks for GC roots.
// Called during garbage collection.
void mp_thread_gc_others(void) {
    mp_thread_mutex_lock(&thread_mutex, 1);

    TaskHandle_t current = xTaskGetCurrentTaskHandle();

    for (mp_thread_t *th = MP_STATE_VM(mp_thread_list_head); th != NULL; th = th->next) {
        // Scan the thread structure itself (contains arg pointer)
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

        // Skip threads with no stack (main thread with external stack)
        if (th->stack == NULL) {
            continue;
        }

        // Scan entire stack buffer for GC roots
        gc_collect_root(th->stack, th->stack_len);
    }

    mp_thread_mutex_unlock(&thread_mutex);
}

#endif // MICROPY_PY_THREAD
