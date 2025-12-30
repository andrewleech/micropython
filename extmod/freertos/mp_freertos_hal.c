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

#include "FreeRTOS.h"
#include "task.h"

#include "py/mpthread.h"
#include "py/runtime.h"
#include "extmod/freertos/mp_freertos_hal.h"

// Task handle of the main Python thread, used for scheduler notifications.
// This handle remains valid across soft resets since the main task persists.
static TaskHandle_t mp_main_task_handle;

// Initialize the FreeRTOS HAL. Must be called from the main Python task.
void mp_freertos_hal_init(void) {
    mp_main_task_handle = xTaskGetCurrentTaskHandle();
}

// Check if currently in ISR context.
// Architecture-specific implementations required for each supported platform.
static inline bool mp_freertos_in_isr(void) {
    #if defined(__arm__) || defined(__thumb__)
    // ARM Cortex-M: Read IPSR register. Non-zero means we're in an exception handler.
    uint32_t ipsr;
    __asm volatile ("mrs %0, ipsr" : "=r" (ipsr));
    return ipsr != 0;
    #elif defined(__riscv)
    // RISC-V: Check mcause or use FreeRTOS port-specific function if available.
    // For now, assume task context. ISR signaling from RISC-V requires port support.
    // TODO: Implement proper RISC-V ISR detection when RP2350 RISC-V support is added.
    return false;
    #else
    #error "mp_freertos_in_isr: unsupported architecture"
    #endif
}

// Signal that a scheduler event is pending. Called from MICROPY_SCHED_HOOK_SCHEDULED.
// Safe to call from ISR or task context. Always signals the main Python thread
// regardless of which thread/ISR the caller is running in.
void mp_freertos_signal_sched_event(void) {
    if (mp_main_task_handle == NULL) {
        return;  // Not initialized yet
    }

    // Use the simple give/take notification pattern for wake-up signaling.
    if (mp_freertos_in_isr()) {
        BaseType_t xHigherPriorityTaskWoken = pdFALSE;
        vTaskNotifyGiveFromISR(mp_main_task_handle, &xHigherPriorityTaskWoken);
        portYIELD_FROM_ISR(xHigherPriorityTaskWoken);
    } else {
        xTaskNotifyGive(mp_main_task_handle);
    }
}

// FreeRTOS-aware millisecond delay.
// Releases the GIL during the delay to allow other threads to run.
// Wakes immediately when callbacks are scheduled via mp_sched_schedule().
void mp_freertos_delay_ms(mp_uint_t ms) {
    if (xTaskGetSchedulerState() != taskSCHEDULER_RUNNING) {
        // Scheduler not running, use busy-wait fallback.
        // This path is only used during early init before vTaskStartScheduler().
        // Timing is approximate; hardware timer not yet available.
        #if defined(mp_hal_delay_us)
        mp_hal_delay_us(ms * 1000);
        #else
        // Crude busy-wait. Assume ~8 cycles per iteration at 125MHz = ~64ns.
        // For 1ms we need ~15625 iterations. Scale by ms.
        volatile uint32_t count = ms * 15625;
        while (count--) {
            __asm volatile ("nop");
        }
        #endif
        return;
    }

    TickType_t start_tick = xTaskGetTickCount();
    TickType_t target_ticks = pdMS_TO_TICKS(ms);

    // Process any already-pending callbacks before entering wait loop
    mp_handle_pending(true);

    while (1) {
        // Calculate remaining time
        TickType_t elapsed = xTaskGetTickCount() - start_tick;
        if (elapsed >= target_ticks) {
            break;
        }
        TickType_t remaining = target_ticks - elapsed;

        // Release GIL and wait for notification or timeout
        MP_THREAD_GIL_EXIT();
        ulTaskNotifyTake(pdTRUE, remaining);
        MP_THREAD_GIL_ENTER();

        // Process callbacks after waking (notification or timeout)
        mp_handle_pending(true);
    }
}

// Get milliseconds since boot using FreeRTOS tick count.
// Resolution is limited to tick rate (typically 1ms for 1kHz tick).
// Wraps after ~49.7 days at 1kHz tick rate (32-bit overflow).
mp_uint_t mp_freertos_ticks_ms(void) {
    return xTaskGetTickCount() * portTICK_PERIOD_MS;
}

// Get microseconds since boot.
// WARNING: This uses tick count scaled to microseconds. Resolution is poor
// (typically 1ms granularity). Wraps after ~71 minutes due to 32-bit overflow.
// Ports needing accurate microsecond timing should override with hardware timer.
mp_uint_t mp_freertos_ticks_us(void) {
    return xTaskGetTickCount() * (portTICK_PERIOD_MS * 1000);
}

// Yield to other tasks. Used by MICROPY_EVENT_POLL_HOOK and MICROPY_THREAD_YIELD.
void mp_freertos_yield(void) {
    if (xTaskGetSchedulerState() == taskSCHEDULER_RUNNING) {
        taskYIELD();
    }
}

#endif // MICROPY_PY_THREAD
