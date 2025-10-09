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

#ifndef MICROPY_INCLUDED_EXTMOD_ZEPHYR_BLE_HAL_ZEPHYR_BLE_WORK_H
#define MICROPY_INCLUDED_EXTMOD_ZEPHYR_BLE_HAL_ZEPHYR_BLE_WORK_H

#include <stdint.h>
#include <stdbool.h>
#include "zephyr_ble_timer.h"

// Zephyr k_work abstraction layer for MicroPython
// Maps Zephyr work queue API to event queue processed by scheduler

// Forward declarations
struct k_work;
struct k_work_q;
struct k_work_delayable;

// Work handler function type
typedef void (*k_work_handler_t)(struct k_work *work);

// Basic work item (similar to NimBLE ble_npl_event)
struct k_work {
    k_work_handler_t handler;
    void *user_data;
    bool pending;
    struct k_work *next;
    struct k_work *prev;
};

// Work queue (similar to NimBLE ble_npl_eventq)
struct k_work_q {
    struct k_work *head;
    struct k_work_q *nextq;
    const char *name;
};

// Delayable work (work + timer)
struct k_work_delayable {
    struct k_work work;
    struct k_timer timer;
    struct k_work_q *queue;
};

// Timeout types
typedef struct {
    uint32_t ticks;
} k_timeout_t;

#define K_NO_WAIT ((k_timeout_t){ .ticks = 0 })
#define K_FOREVER ((k_timeout_t){ .ticks = 0xFFFFFFFF })
#define K_MSEC(ms) ((k_timeout_t){ .ticks = (ms) })
#define K_SECONDS(s) K_MSEC((s) * 1000)

// Timeout conversion helper
static inline uint32_t k_timeout_to_ms(k_timeout_t timeout) {
    return timeout.ticks;
}

// Work queue configuration
struct k_work_queue_config {
    const char *name;
};

// --- Work Queue API ---

// Initialize work item
void k_work_init(struct k_work *work, k_work_handler_t handler);

// Initialize work queue
void k_work_queue_init(struct k_work_q *queue);

// Start work queue (no-op in MicroPython, as we don't create threads)
void k_work_queue_start(struct k_work_q *queue, void *stack, size_t stack_size, int prio, const struct k_work_queue_config *cfg);

// Submit work to queue
int k_work_submit(struct k_work *work);
int k_work_submit_to_queue(struct k_work_q *queue, struct k_work *work);

// Cancel work
int k_work_cancel(struct k_work *work);
int k_work_cancel_sync(struct k_work *work, void *sync);

// Check if work is pending
bool k_work_is_pending(const struct k_work *work);

// --- Delayable Work API ---

// Initialize delayable work
void k_work_init_delayable(struct k_work_delayable *dwork, k_work_handler_t handler);

// Schedule delayable work
int k_work_schedule(struct k_work_delayable *dwork, k_timeout_t delay);
int k_work_schedule_for_queue(struct k_work_q *queue, struct k_work_delayable *dwork, k_timeout_t delay);

// Reschedule delayable work
int k_work_reschedule(struct k_work_delayable *dwork, k_timeout_t delay);
int k_work_reschedule_for_queue(struct k_work_q *queue, struct k_work_delayable *dwork, k_timeout_t delay);

// Cancel delayable work
int k_work_cancel_delayable(struct k_work_delayable *dwork);
int k_work_cancel_delayable_sync(struct k_work_delayable *dwork, void *sync);

// Get remaining time
k_ticks_t k_work_delayable_remaining_get(const struct k_work_delayable *dwork);

// Check if delayable work is pending
bool k_work_delayable_is_pending(const struct k_work_delayable *dwork);

// Get delayable work busy status
int k_work_delayable_busy_get(const struct k_work_delayable *dwork);

// --- MicroPython-specific functions ---

// Called by MicroPython scheduler to process all pending work
void mp_bluetooth_zephyr_work_process(void);

// Get work from delayable work (for internal use)
static inline struct k_work *k_work_delayable_from_work(struct k_work *work) {
    return work;
}

#define CONTAINER_OF(ptr, type, member) \
    ((type *)(((char *)(ptr)) - offsetof(type, member)))

#endif // MICROPY_INCLUDED_EXTMOD_ZEPHYR_BLE_HAL_ZEPHYR_BLE_WORK_H
