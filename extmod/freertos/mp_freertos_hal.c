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
#include "extmod/freertos/mp_freertos_hal.h"

// FreeRTOS-aware millisecond delay.
// Releases the GIL during the delay to allow other threads to run.
void mp_freertos_delay_ms(mp_uint_t ms) {
    if (xTaskGetSchedulerState() == taskSCHEDULER_RUNNING) {
        MP_THREAD_GIL_EXIT();
        if (ms > 0) {
            vTaskDelay(pdMS_TO_TICKS(ms));
        } else {
            taskYIELD();
        }
        MP_THREAD_GIL_ENTER();
    } else {
        // Scheduler not running, use busy-wait fallback.
        // This path is used during early init before vTaskStartScheduler().
        // Port should provide mp_hal_delay_us() for accurate timing.
        #if defined(mp_hal_delay_us)
        mp_hal_delay_us(ms * 1000);
        #else
        // Crude busy-wait; actual delay depends on CPU speed.
        volatile uint32_t count = ms * 1000;
        while (count--) {
            __asm volatile ("nop");
        }
        #endif
    }
}

// Microsecond delay.
// This is a busy-wait implementation. Ports requiring precise microsecond
// timing should override with a hardware timer implementation.
void mp_freertos_delay_us(mp_uint_t us) {
    // Simple busy-wait loop. Accuracy depends on CPU speed.
    // Each iteration is roughly 4-10 cycles depending on architecture.
    // For 100MHz CPU: 1us ≈ 100 cycles ≈ 10-25 iterations.
    // This is intentionally imprecise; ports needing accuracy should
    // provide their own mp_hal_delay_us() using DWT or hardware timer.
    volatile uint32_t count = us * 10;  // Approximate for ~100MHz
    while (count--) {
        __asm volatile ("nop");
    }
}

// Get milliseconds since boot using FreeRTOS tick count.
// Resolution is limited to tick rate (typically 1ms for 1kHz tick).
mp_uint_t mp_freertos_ticks_ms(void) {
    return xTaskGetTickCount() * portTICK_PERIOD_MS;
}

// Get microseconds since boot.
// This uses the tick count scaled to microseconds. Resolution is poor
// (typically 1ms). Ports needing microsecond precision should override
// with a hardware timer implementation (DWT, SysTick, or dedicated timer).
mp_uint_t mp_freertos_ticks_us(void) {
    // Scale tick count to microseconds. Limited by tick resolution.
    return xTaskGetTickCount() * (portTICK_PERIOD_MS * 1000);
}

// Yield to other tasks. Used by MICROPY_EVENT_POLL_HOOK and MICROPY_THREAD_YIELD.
// NOTE: For event polling, we delay for 10ms to allow lower-priority tasks
// (like the service task) adequate CPU time. This is a compromise since
// MICROPY_EVENT_POLL_HOOK doesn't have access to the timeout parameter.
void mp_freertos_yield(void) {
    if (xTaskGetSchedulerState() == taskSCHEDULER_RUNNING) {
        vTaskDelay(pdMS_TO_TICKS(10));  // Wait 10ms to allow lower-priority tasks to run
    }
}

#endif // MICROPY_PY_THREAD
