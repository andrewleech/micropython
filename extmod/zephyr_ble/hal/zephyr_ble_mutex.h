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

#ifndef MICROPY_INCLUDED_EXTMOD_ZEPHYR_BLE_HAL_ZEPHYR_BLE_MUTEX_H
#define MICROPY_INCLUDED_EXTMOD_ZEPHYR_BLE_HAL_ZEPHYR_BLE_MUTEX_H

#include "py/mpconfig.h"
#include <stdint.h>
#include "zephyr_ble_work.h"

// Zephyr k_mutex abstraction layer for MicroPython
//
// When MICROPY_PY_THREAD is enabled (FreeRTOS available):
//   Uses FreeRTOS mutex with priority inheritance
//
// When MICROPY_PY_THREAD is disabled (no RTOS):
//   No-op implementation - single-threaded execution provides mutual exclusion

#if MICROPY_PY_THREAD
// FreeRTOS-backed mutex with static allocation
#include "FreeRTOS.h"
#include "semphr.h"

struct k_mutex {
    SemaphoreHandle_t handle;       // FreeRTOS mutex handle
    StaticSemaphore_t storage;      // Static storage for mutex
};

#else
// No-op mutex (fallback for single-threaded execution)
struct k_mutex {
    volatile uint8_t locked;        // Lock count for debugging
};

#endif // MICROPY_PY_THREAD

// --- Mutex API ---

// Initialize mutex
void k_mutex_init(struct k_mutex *mutex);

// Lock mutex (always succeeds in scheduler context)
int k_mutex_lock(struct k_mutex *mutex, k_timeout_t timeout);

// Unlock mutex (returns 0 on success)
int k_mutex_unlock(struct k_mutex *mutex);

#endif // MICROPY_INCLUDED_EXTMOD_ZEPHYR_BLE_HAL_ZEPHYR_BLE_MUTEX_H
