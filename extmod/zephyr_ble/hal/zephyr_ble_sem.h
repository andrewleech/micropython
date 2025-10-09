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

#include <stdint.h>
#include "zephyr_ble_work.h"

// Zephyr k_sem abstraction layer for MicroPython
// Maps Zephyr semaphore API to busy-wait pattern (like NimBLE)

// Semaphore structure
struct k_sem {
    volatile uint16_t count;
    uint16_t limit;
};

// Error codes
#define EAGAIN 11
#define EBUSY  16

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

#endif // MICROPY_INCLUDED_EXTMOD_ZEPHYR_BLE_HAL_ZEPHYR_BLE_SEM_H
