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

#ifndef MICROPY_INCLUDED_EXTMOD_ZEPHYR_BLE_HAL_ZEPHYR_BLE_TIMER_H
#define MICROPY_INCLUDED_EXTMOD_ZEPHYR_BLE_HAL_ZEPHYR_BLE_TIMER_H

#include <stdint.h>
#include <stdbool.h>

// Zephyr k_timer abstraction layer for MicroPython
// Maps Zephyr timer API to MicroPython timer mechanism

typedef void (*k_timer_expiry_t)(struct k_timer *timer);

struct k_timer {
    bool active;
    uint32_t expiry_ticks;
    k_timer_expiry_t expiry_fn;
    void *user_data;
    struct k_timer *next;
};

// Zephyr timer API
void k_timer_init(struct k_timer *timer, k_timer_expiry_t expiry_fn, k_timer_expiry_t stop_fn);
void k_timer_start(struct k_timer *timer, uint32_t duration_ms, uint32_t period_ms);
void k_timer_stop(struct k_timer *timer);

// Note: K_MSEC, K_NO_WAIT, K_FOREVER are defined in zephyr_ble_work.h

// k_yield() abstraction
static inline void k_yield(void) {
    // In MicroPython all BLE code runs in scheduler context,
    // so yield is handled by MICROPY_EVENT_POLL_HOOK
    #ifdef MICROPY_EVENT_POLL_HOOK
    MICROPY_EVENT_POLL_HOOK
    #endif
}

// Called by MicroPython scheduler to process timer callbacks
void mp_bluetooth_zephyr_timer_process(void);

#endif // MICROPY_INCLUDED_EXTMOD_ZEPHYR_BLE_HAL_ZEPHYR_BLE_TIMER_H
