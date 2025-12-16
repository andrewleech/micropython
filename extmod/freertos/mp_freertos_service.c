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

#if MICROPY_PY_THREAD && MICROPY_FREERTOS_SERVICE_TASKS

#include "extmod/freertos/mp_freertos_service.h"

#include "FreeRTOS.h"
#include "task.h"

// ============================================================================
// Debug instrumentation (temporary - remove after debugging)
// ============================================================================
#ifndef MP_FREERTOS_SERVICE_DEBUG
#define MP_FREERTOS_SERVICE_DEBUG (1)
#endif

#if MP_FREERTOS_SERVICE_DEBUG
#include "py/mpprint.h"

// Debug counters (function implementations at end of file)
static volatile uint32_t dbg_notify_count;        // Times service task was notified
static volatile uint32_t dbg_wakeup_count;        // Times service task woke up
static volatile uint32_t dbg_suspended_skip;      // Times skipped due to suspend
static volatile uint32_t dbg_dispatch_count[MICROPY_FREERTOS_SERVICE_MAX_SLOTS];  // Per-slot dispatch count
static volatile uint32_t dbg_schedule_isr;        // Schedules from ISR context
static volatile uint32_t dbg_schedule_task;       // Schedules from task context
static volatile int dbg_suspend_max;              // Max suspend_count seen
#endif // MP_FREERTOS_SERVICE_DEBUG

// ============================================================================
// Service Task Implementation
// ============================================================================

// Stack size in StackType_t units
#define SERVICE_TASK_STACK_WORDS (MICROPY_FREERTOS_SERVICE_STACK_SIZE / sizeof(StackType_t))

// Static allocation for service task
static StaticTask_t service_task_tcb;
static StackType_t service_task_stack[SERVICE_TASK_STACK_WORDS];
static TaskHandle_t service_task_handle;

// Dispatch table - volatile to prevent compiler optimization across ISR/task boundary
static volatile mp_freertos_dispatch_t dispatch_table[MICROPY_FREERTOS_SERVICE_MAX_SLOTS];

// Suspend counter - like original PendSV implementation
// When > 0, service task should not process dispatches
static volatile int suspend_count;

// Service task function - waits for notifications and processes dispatch table
static void service_task_entry(void *arg) {
    (void)arg;

    for (;;) {
        // Block until notified (efficient, doesn't spin)
        ulTaskNotifyTake(pdTRUE, portMAX_DELAY);

        #if MP_FREERTOS_SERVICE_DEBUG
        dbg_wakeup_count++;
        if (suspend_count > dbg_suspend_max) {
            dbg_suspend_max = suspend_count;
        }
        #endif

        // If suspended, just consume the notification and wait again.
        // The resume function will re-notify us if work is pending.
        if (suspend_count > 0) {
            #if MP_FREERTOS_SERVICE_DEBUG
            dbg_suspended_skip++;
            #endif
            continue;
        }

        // Process all pending dispatches
        for (size_t i = 0; i < MICROPY_FREERTOS_SERVICE_MAX_SLOTS; ++i) {
            mp_freertos_dispatch_t f = __atomic_exchange_n(&dispatch_table[i], NULL, __ATOMIC_SEQ_CST);
            if (f != NULL) {
                #if MP_FREERTOS_SERVICE_DEBUG
                dbg_dispatch_count[i]++;
                #endif
                f();
            }
        }
    }
}

void mp_freertos_service_init(void) {
    // Thread-safe initialization guard for SMP using atomic exchange
    static volatile uint32_t initialized = 0;
    uint32_t was_init = __atomic_exchange_n(&initialized, 1, __ATOMIC_SEQ_CST);
    if (was_init) {
        return;  // Already initialized by another core
    }

    // Create service task with static allocation
    service_task_handle = xTaskCreateStatic(
        service_task_entry,
        "svc",
        SERVICE_TASK_STACK_WORDS,
        NULL,
        MICROPY_FREERTOS_SERVICE_PRIORITY,
        service_task_stack,
        &service_task_tcb);
}

void mp_freertos_service_schedule(size_t slot, mp_freertos_dispatch_t callback) {
    dispatch_table[slot] = callback;

    // Use correct FreeRTOS API based on context
    if (mp_freertos_service_in_isr()) {
        #if MP_FREERTOS_SERVICE_DEBUG
        dbg_schedule_isr++;
        dbg_notify_count++;
        #endif
        BaseType_t xHigherPriorityTaskWoken = pdFALSE;
        vTaskNotifyGiveFromISR(service_task_handle, &xHigherPriorityTaskWoken);
        portYIELD_FROM_ISR(xHigherPriorityTaskWoken);
    } else {
        #if MP_FREERTOS_SERVICE_DEBUG
        dbg_schedule_task++;
        dbg_notify_count++;
        #endif
        xTaskNotifyGive(service_task_handle);
    }
}

void mp_freertos_service_suspend(void) {
    // Atomically increment suspend counter
    __atomic_add_fetch(&suspend_count, 1, __ATOMIC_SEQ_CST);
}

void mp_freertos_service_resume(void) {
    // Atomically decrement suspend counter
    int new_count = __atomic_sub_fetch(&suspend_count, 1, __ATOMIC_SEQ_CST);

    // Re-notify service task if any dispatch is pending and we're no longer suspended
    if (new_count == 0) {
        for (size_t i = 0; i < MICROPY_FREERTOS_SERVICE_MAX_SLOTS; ++i) {
            if (dispatch_table[i] != NULL) {
                xTaskNotifyGive(service_task_handle);
                break;
            }
        }
    }
}

bool mp_freertos_service_is_pending(size_t slot) {
    return dispatch_table[slot] != NULL;
}

// ============================================================================
// Debug function implementations (after suspend_count is declared)
// ============================================================================
#if MP_FREERTOS_SERVICE_DEBUG
void mp_freertos_service_debug_print(void) {
    mp_printf(&mp_plat_print, "=== Service Task Debug Stats ===\n");
    mp_printf(&mp_plat_print, "notify_count:    %lu\n", (unsigned long)dbg_notify_count);
    mp_printf(&mp_plat_print, "wakeup_count:    %lu\n", (unsigned long)dbg_wakeup_count);
    mp_printf(&mp_plat_print, "suspended_skip:  %lu\n", (unsigned long)dbg_suspended_skip);
    mp_printf(&mp_plat_print, "schedule_isr:    %lu\n", (unsigned long)dbg_schedule_isr);
    mp_printf(&mp_plat_print, "schedule_task:   %lu\n", (unsigned long)dbg_schedule_task);
    mp_printf(&mp_plat_print, "suspend_count:   %d (max seen: %d)\n", suspend_count, dbg_suspend_max);
    for (size_t i = 0; i < MICROPY_FREERTOS_SERVICE_MAX_SLOTS; ++i) {
        mp_printf(&mp_plat_print, "  slot[%u]:       %lu dispatches\n", (unsigned)i, (unsigned long)dbg_dispatch_count[i]);
    }
    mp_printf(&mp_plat_print, "================================\n");
}

void mp_freertos_service_debug_reset(void) {
    dbg_notify_count = 0;
    dbg_wakeup_count = 0;
    dbg_suspended_skip = 0;
    dbg_schedule_isr = 0;
    dbg_schedule_task = 0;
    dbg_suspend_max = 0;
    for (size_t i = 0; i < MICROPY_FREERTOS_SERVICE_MAX_SLOTS; ++i) {
        dbg_dispatch_count[i] = 0;
    }
}
#endif // MP_FREERTOS_SERVICE_DEBUG

#endif // MICROPY_PY_THREAD && MICROPY_FREERTOS_SERVICE_TASKS
