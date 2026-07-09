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

#include "py/mpconfig.h"
#include <stdint.h>
#include <stdbool.h>

#ifdef __ZEPHYR__
// Native Zephyr: use real kernel types
#include <zephyr/kernel.h>
#else

// Zephyr k_timer abstraction layer for MicroPython
//
// When MICROPY_PY_THREAD is enabled (FreeRTOS available):
//   Uses FreeRTOS software timers for proper timing
//
// When MICROPY_PY_THREAD is disabled (no RTOS):
//   Uses polling-based timer mechanism

// Forward declaration
struct k_timer;

typedef void (*k_timer_expiry_t)(struct k_timer *timer);

#if MICROPY_PY_THREAD
// FreeRTOS-backed timer with static allocation
#include "FreeRTOS.h"
#include "timers.h"

struct k_timer {
    TimerHandle_t handle;           // FreeRTOS timer handle
    StaticTimer_t storage;          // Static storage for timer
    k_timer_expiry_t expiry_fn;     // Expiry callback
    void *user_data;                // User data
};

#else
// Polling-based timer (fallback for no RTOS)
struct k_timer {
    bool active;
    uint32_t expiry_ticks;
    k_timer_expiry_t expiry_fn;
    void *user_data;
    struct k_timer *next;           // Linked list for polling
};

#endif // MICROPY_PY_THREAD

// Forward declare k_timeout_t (defined in zephyr_ble_work.h)
typedef struct {
    uint32_t ticks;
} k_timeout_t;

// Zephyr timer API
void k_timer_init(struct k_timer *timer, k_timer_expiry_t expiry_fn, k_timer_expiry_t stop_fn);
void k_timer_start(struct k_timer *timer, k_timeout_t duration, k_timeout_t period);
void k_timer_stop(struct k_timer *timer);

// Note: K_MSEC, K_NO_WAIT, K_FOREVER are defined in zephyr_ble_work.h
// Note: k_yield() is defined in zephyr_ble_kernel.h

// Check if two timeouts are equal
static inline bool K_TIMEOUT_EQ(k_timeout_t a, k_timeout_t b) {
    return a.ticks == b.ticks;
}

#endif // __ZEPHYR__

// Called by MicroPython scheduler to process timer callbacks
void mp_bluetooth_zephyr_timer_process(void);

#endif // MICROPY_INCLUDED_EXTMOD_ZEPHYR_BLE_HAL_ZEPHYR_BLE_TIMER_H
