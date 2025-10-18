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

#include "py/mphal.h"
#include "py/runtime.h"
#include "zephyr_ble_timer.h"

#if ZEPHYR_BLE_DEBUG
#define DEBUG_TIMER_printf(...) mp_printf(&mp_plat_print, "TIMER: " __VA_ARGS__)
#else
#define DEBUG_TIMER_printf(...) do {} while (0)
#endif

// Global linked list of timers (similar to NimBLE callouts)
static struct k_timer *global_timer = NULL;

void k_timer_init(struct k_timer *timer, k_timer_expiry_t expiry_fn, k_timer_expiry_t stop_fn) {
    DEBUG_TIMER_printf("k_timer_init(%p, %p, %p)\n", timer, expiry_fn, stop_fn);

    // Note: stop_fn is not used in current modbluetooth_zephyr.c implementation
    timer->active = false;
    timer->expiry_ticks = 0;
    timer->expiry_fn = expiry_fn;
    timer->user_data = NULL;

    // Add to global timer list if not already present
    struct k_timer **t;
    for (t = &global_timer; *t != NULL; t = &(*t)->next) {
        if (*t == timer) {
            // Already in list
            return;
        }
    }
    *t = timer;
    timer->next = NULL;
}

void k_timer_start(struct k_timer *timer, k_timeout_t duration, k_timeout_t period) {
    DEBUG_TIMER_printf("k_timer_start(%p, %u, %u) tnow=%u\n",
        timer, (unsigned)duration.ticks, (unsigned)period.ticks,
        (unsigned)mp_hal_ticks_ms());

    // Note: period is not used in current modbluetooth_zephyr.c (always K_NO_WAIT)
    timer->active = true;
    timer->expiry_ticks = mp_hal_ticks_ms() + duration.ticks;
}

void k_timer_stop(struct k_timer *timer) {
    DEBUG_TIMER_printf("k_timer_stop(%p)\n", timer);
    timer->active = false;
}

// Called periodically by MicroPython to process timer callbacks
void mp_bluetooth_zephyr_timer_process(void) {
    uint32_t tnow = mp_hal_ticks_ms();

    for (struct k_timer *timer = global_timer; timer != NULL; timer = timer->next) {
        if (!timer->active) {
            continue;
        }

        if ((int32_t)(tnow - timer->expiry_ticks) >= 0) {
            DEBUG_TIMER_printf("timer_expire(%p) tnow=%u expiry=%u\n",
                timer, (unsigned)tnow, (unsigned)timer->expiry_ticks);
            timer->active = false;

            // Call the expiry callback
            if (timer->expiry_fn) {
                timer->expiry_fn(timer);
            }
        }
    }
}
