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

#ifndef MICROPY_INCLUDED_EXTMOD_ZEPHYR_BLE_HAL_ZEPHYR_BLE_SEM_H
#define MICROPY_INCLUDED_EXTMOD_ZEPHYR_BLE_HAL_ZEPHYR_BLE_SEM_H

#include "py/mpconfig.h"
#include <stdint.h>
#include <errno.h>
#include "zephyr_ble_work.h"

// Zephyr k_sem abstraction layer for MicroPython
//
// When MICROPY_PY_THREAD is enabled (FreeRTOS available):
//   Uses real FreeRTOS counting semaphores with true blocking
//
// When MICROPY_PY_THREAD is disabled (no RTOS):
//   Falls back to polling-based implementation

#if MICROPY_PY_THREAD
// FreeRTOS-backed semaphore with static allocation
#include "FreeRTOS.h"
#include "semphr.h"

struct k_sem {
    SemaphoreHandle_t handle;       // FreeRTOS semaphore handle
    StaticSemaphore_t storage;      // Static storage for semaphore
    uint16_t limit;                 // Maximum count (for reference)
};

#else
// Polling-based semaphore (fallback for non-RTOS ports)
struct k_sem {
    volatile uint16_t count;
    uint16_t limit;
};

#endif // MICROPY_PY_THREAD

// --- Semaphore API ---

// Initialize semaphore
void k_sem_init(struct k_sem *sem, unsigned int initial_count, unsigned int limit);

// Take semaphore (blocking with timeout)
// Returns 0 on success, -EAGAIN on timeout
int k_sem_take(struct k_sem *sem, k_timeout_t timeout);

// Give semaphore
void k_sem_give(struct k_sem *sem);

// Get semaphore count
unsigned int k_sem_count_get(struct k_sem *sem);

// Reset semaphore to initial count
void k_sem_reset(struct k_sem *sem);

// Semaphore maximum limit constant
#define K_SEM_MAX_LIMIT UINT16_MAX

// Static semaphore definition macro
// Note: FreeRTOS semaphores require runtime initialization via k_sem_init()
// The K_SEM_DEFINE macro creates the structure but does NOT initialize it.
// Call k_sem_init() before use.
#if MICROPY_PY_THREAD
#define K_SEM_DEFINE(name, initial_count, max_limit) \
    struct k_sem name = { \
        .handle = NULL, \
        .limit = (max_limit) \
    }
#else
#define K_SEM_DEFINE(name, initial_count, max_limit) \
    struct k_sem name = { \
        .count = (initial_count), \
        .limit = (max_limit) \
    }
#endif

// Check if we're in ISR context (for k_sem_give from ISR)
// This is provided by the port via mp_freertos_service_in_isr() when available
#if MICROPY_PY_THREAD && MICROPY_FREERTOS_SERVICE_TASKS
#include "extmod/freertos/mp_freertos_service.h"
#define k_sem_in_isr() mp_freertos_service_in_isr()
#else
#define k_sem_in_isr() (0)
#endif

#endif // MICROPY_INCLUDED_EXTMOD_ZEPHYR_BLE_HAL_ZEPHYR_BLE_SEM_H
