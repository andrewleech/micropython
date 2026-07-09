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

#ifndef MICROPY_INCLUDED_EXTMOD_ZEPHYR_BLE_HAL_ZEPHYR_BLE_FIFO_H
#define MICROPY_INCLUDED_EXTMOD_ZEPHYR_BLE_HAL_ZEPHYR_BLE_FIFO_H

#include <stdint.h>
#include <stdbool.h>
#include <zephyr/kernel.h>  // For k_queue, k_fifo, k_lifo definitions
#include "zephyr_ble_timer.h"

// Zephyr k_fifo abstraction for MicroPython
// Maps Zephyr FIFO queue API to simple linked list

// --- k_queue API (underlying implementation) ---

// k_queue, k_fifo, k_lifo structures are defined in kernel.h
// All structures have sys_slist_t data_q member
//
// kernel.h provides:
//   - k_fifo_init(), k_fifo_get(), k_fifo_put(), k_fifo_is_empty() etc.
//   - k_lifo_init()
//   - k_queue_prepend()
//
// We implement the following helper functions:

// --- k_queue API (internal helpers) ---

void k_queue_init(struct k_queue *queue);
void k_queue_append(struct k_queue *queue, void *data);
void *k_queue_get(struct k_queue *queue, k_timeout_t timeout);

// --- LIFO API ---

// Put item into LIFO (head)
void k_lifo_put(struct k_lifo *lifo, void *data);

// Get item from LIFO (head) with timeout
void *k_lifo_get(struct k_lifo *lifo, k_timeout_t timeout);

// --- Debug Support ---

// Enable debug output for FIFO operations (call after boot)
void mp_bluetooth_zephyr_fifo_enable_debug(void);

#endif // MICROPY_INCLUDED_EXTMOD_ZEPHYR_BLE_HAL_ZEPHYR_BLE_FIFO_H
