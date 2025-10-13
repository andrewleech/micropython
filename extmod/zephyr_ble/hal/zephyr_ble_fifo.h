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
#include "zephyr_ble_timer.h"

// Zephyr k_fifo abstraction for MicroPython
// Maps Zephyr FIFO queue API to simple linked list

// --- k_queue API (underlying implementation) ---

// Queue structure (simple linked list)
// Must be defined before k_fifo since k_fifo contains it
struct k_queue {
    void *head;
    void *tail;
};

// FIFO queue structure (must match Zephyr's structure layout)
// Zephyr's k_fifo wraps k_queue, and k_fifo_init is a macro
// that calls k_queue_init(&fifo->_queue)
struct k_fifo {
    struct k_queue _queue;  // Zephyr expects this member
};

// LIFO queue structure (must match Zephyr's structure layout)
// Zephyr's k_lifo wraps k_queue just like k_fifo
// LIFO = Last In First Out (stack behavior)
struct k_lifo {
    struct k_queue _queue;  // Zephyr expects this member
};

// Initialize queue
void k_queue_init(struct k_queue *queue);

// Append item to queue
void k_queue_append(struct k_queue *queue, void *data);

// Get item from queue with timeout
void *k_queue_get(struct k_queue *queue, k_timeout_t timeout);

// Check if queue is empty
static inline bool k_queue_is_empty(struct k_queue *queue) {
    return queue->head == NULL;
}

// --- FIFO API ---

// Note: k_fifo_init is a macro in Zephyr that calls k_queue_init(&fifo->_queue)
// We provide the k_queue_init implementation above

// Initialize FIFO queue (wrapper for compatibility)
static inline void k_fifo_init_impl(struct k_fifo *fifo) {
    k_queue_init(&fifo->_queue);
}

// Put item into FIFO (tail) - wrapper for k_queue_append
static inline void k_fifo_put(struct k_fifo *fifo, void *data) {
    k_queue_append(&fifo->_queue, data);
}

// Get item from FIFO (head), wait with timeout - wrapper for k_queue_get
static inline void *k_fifo_get(struct k_fifo *fifo, k_timeout_t timeout) {
    return k_queue_get(&fifo->_queue, timeout);
}

// Check if FIFO is empty - wrapper for k_queue_is_empty
static inline bool k_fifo_is_empty(struct k_fifo *fifo) {
    return k_queue_is_empty(&fifo->_queue);
}

// Peek at head of FIFO without removing
static inline void *k_fifo_peek_head(struct k_fifo *fifo) {
    return fifo->_queue.head;
}

// Peek at tail of FIFO without removing
static inline void *k_fifo_peek_tail(struct k_fifo *fifo) {
    return fifo->_queue.tail;
}

// Cancel waiting on FIFO (no-op in MicroPython)
static inline void k_fifo_cancel_wait(struct k_fifo *fifo) {
    (void)fifo;
}

// --- Debug Support ---

// Enable debug output for FIFO operations (call after boot)
void mp_bluetooth_zephyr_fifo_enable_debug(void);

#endif // MICROPY_INCLUDED_EXTMOD_ZEPHYR_BLE_HAL_ZEPHYR_BLE_FIFO_H
