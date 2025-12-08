/*
 * This file is part of the MicroPython project, http://micropython.org/
 *
 * The MIT License (MIT)
 *
 * Copyright (c) 2022 Damien P. George
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

#include <assert.h>
#include "py/mpconfig.h"
#include "pendsv.h"

#if MICROPY_PY_THREAD
#include "py/mpthread.h"
#endif

#if PICO_RP2040
#include "RP2040.h"
#elif PICO_RP2350 && PICO_ARM
#include "RP2350.h"
#elif PICO_RISCV
#include "pico/aon_timer.h"
#endif

#if MICROPY_PY_NETWORK_CYW43
#include "lib/cyw43-driver/src/cyw43_stats.h"
#endif

static pendsv_dispatch_t pendsv_dispatch_table[PENDSV_DISPATCH_NUM_SLOTS];

#if MICROPY_PY_THREAD

// ============================================================================
// FreeRTOS Service Task Implementation
//
// Instead of using PendSV interrupt (which conflicts with FreeRTOS's use of
// PendSV for context switching), we use a high-priority service task that
// processes the dispatch table when signaled via task notifications.
//
// This approach:
// - Eliminates PendSV handler conflicts with FreeRTOS
// - Works correctly with FreeRTOS SMP on dual-core RP2040
// - Maintains the same pendsv_schedule_dispatch() API
// - Provides similar timing characteristics (task runs as soon as possible)
// ============================================================================

#include "FreeRTOS.h"
#include "task.h"

// Service task runs at highest priority to emulate "lowest interrupt" behavior.
// It will preempt all other tasks as soon as it's notified.
#define SERVICE_TASK_PRIORITY (configMAX_PRIORITIES - 1)
#define SERVICE_TASK_STACK_SIZE (512 / sizeof(StackType_t))

static StaticTask_t service_task_tcb;
static StackType_t service_task_stack[SERVICE_TASK_STACK_SIZE];
static TaskHandle_t service_task_handle;

// Recursive mutex for suspend/resume mechanism.
// Important to use recursive mutex as either core may call pendsv_suspend()
// and expect both mutual exclusion and that dispatch won't run.
static mp_thread_recursive_mutex_t pendsv_mutex;

// Service task function - waits for notifications and processes dispatch table.
static void pendsv_service_task(void *arg) {
    (void)arg;

    for (;;) {
        // Block until notified (efficient, doesn't spin)
        ulTaskNotifyTake(pdTRUE, portMAX_DELAY);

        #if MICROPY_PY_NETWORK_CYW43
        CYW43_STAT_INC(PENDSV_RUN_COUNT);
        #endif

        // Try to acquire mutex (non-blocking)
        // If suspended, we'll be re-notified when pendsv_resume() is called
        if (!mp_thread_recursive_mutex_lock(&pendsv_mutex, 0)) {
            continue;
        }

        // Process all pending dispatches
        for (size_t i = 0; i < PENDSV_DISPATCH_NUM_SLOTS; ++i) {
            if (pendsv_dispatch_table[i] != NULL) {
                pendsv_dispatch_t f = pendsv_dispatch_table[i];
                pendsv_dispatch_table[i] = NULL;
                f();
            }
        }

        mp_thread_recursive_mutex_unlock(&pendsv_mutex);
    }
}

void pendsv_init(void) {
    static bool initialized = false;

    if (!initialized) {
        initialized = true;
        mp_thread_recursive_mutex_init(&pendsv_mutex);

        // Create service task with static allocation
        service_task_handle = xTaskCreateStatic(
            pendsv_service_task,
            "svc",
            SERVICE_TASK_STACK_SIZE,
            NULL,
            SERVICE_TASK_PRIORITY,
            service_task_stack,
            &service_task_tcb);
    }
}

void pendsv_suspend(void) {
    mp_thread_recursive_mutex_lock(&pendsv_mutex, 1);
}

void pendsv_resume(void) {
    mp_thread_recursive_mutex_unlock(&pendsv_mutex);

    // Check if any dispatch is pending and notify service task
    for (size_t i = 0; i < PENDSV_DISPATCH_NUM_SLOTS; ++i) {
        if (pendsv_dispatch_table[i] != NULL) {
            xTaskNotifyGive(service_task_handle);
            break;
        }
    }
}

// Check if running in interrupt context (Cortex-M: IPSR != 0 means exception/interrupt)
static inline bool pendsv_in_isr(void) {
    uint32_t ipsr;
    __asm volatile ("mrs %0, ipsr" : "=r" (ipsr));
    return ipsr != 0;
}

void pendsv_schedule_dispatch(size_t slot, pendsv_dispatch_t f) {
    pendsv_dispatch_table[slot] = f;

    // Check if we're in ISR context
    if (pendsv_in_isr()) {
        BaseType_t xHigherPriorityTaskWoken = pdFALSE;
        vTaskNotifyGiveFromISR(service_task_handle, &xHigherPriorityTaskWoken);
        portYIELD_FROM_ISR(xHigherPriorityTaskWoken);
    } else {
        xTaskNotifyGive(service_task_handle);
    }
}

#else // !MICROPY_PY_THREAD

// ============================================================================
// Non-threaded Implementation (original PendSV-based approach)
//
// Without FreeRTOS, we use the traditional PendSV interrupt mechanism.
// ============================================================================

#include "hardware/irq.h"

// PendSV IRQ priority, to run system-level tasks that preempt the main thread.
#define IRQ_PRI_PENDSV PICO_LOWEST_IRQ_PRIORITY

static int pendsv_lock;

void pendsv_init(void) {
    #if !defined(__riscv)
    NVIC_SetPriority(PendSV_IRQn, IRQ_PRI_PENDSV);
    #endif
}

void pendsv_suspend(void) {
    pendsv_lock++;
}

void pendsv_resume(void) {
    assert(pendsv_lock > 0);
    pendsv_lock--;

    // Re-trigger if work is pending
    if (pendsv_lock == 0) {
        for (size_t i = 0; i < PENDSV_DISPATCH_NUM_SLOTS; ++i) {
            if (pendsv_dispatch_table[i] != NULL) {
                #if PICO_ARM
                SCB->ICSR = SCB_ICSR_PENDSVSET_Msk;
                #elif PICO_RISCV
                struct timespec ts;
                aon_timer_get_time(&ts);
                aon_timer_enable_alarm(&ts, PendSV_Handler, false);
                #endif
                break;
            }
        }
    }
}

void pendsv_schedule_dispatch(size_t slot, pendsv_dispatch_t f) {
    pendsv_dispatch_table[slot] = f;

    if (pendsv_lock == 0) {
        #if PICO_ARM
        SCB->ICSR = SCB_ICSR_PENDSVSET_Msk;
        #elif PICO_RISCV
        struct timespec ts;
        aon_timer_get_time(&ts);
        aon_timer_enable_alarm(&ts, PendSV_Handler, false);
        #endif
    } else {
        #if MICROPY_PY_NETWORK_CYW43
        CYW43_STAT_INC(PENDSV_DISABLED_COUNT);
        #endif
    }
}

// PendSV interrupt handler for non-threaded builds
void PendSV_Handler(void) {
    assert(pendsv_lock == 0);

    #if MICROPY_PY_NETWORK_CYW43
    CYW43_STAT_INC(PENDSV_RUN_COUNT);
    #endif

    for (size_t i = 0; i < PENDSV_DISPATCH_NUM_SLOTS; ++i) {
        if (pendsv_dispatch_table[i] != NULL) {
            pendsv_dispatch_t f = pendsv_dispatch_table[i];
            pendsv_dispatch_table[i] = NULL;
            f();
        }
    }
}

#endif // MICROPY_PY_THREAD

bool pendsv_is_pending(size_t slot) {
    return pendsv_dispatch_table[slot] != NULL;
}
