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
#include <stddef.h>
#include <stdbool.h>
#include "zephyr_ble_timer.h"

#ifdef __ZEPHYR__
// Native Zephyr: kernel types and work API provided by <zephyr/kernel.h>
// (already included via zephyr_ble_timer.h)
#else

// CONTAINER_OF macro (also defined in kernel.h)
#ifndef CONTAINER_OF
#define CONTAINER_OF(ptr, type, field) \
    ((type *)(((char *)(ptr)) - offsetof(type, field)))
#endif

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
    void *thread;  // Placeholder for thread (always NULL in MicroPython)
};

// Delayable work (work + timer)
struct k_work_delayable {
    struct k_work work;
    struct k_timer timer;
    struct k_work_q *queue;
};

// Note: k_timeout_t is defined in zephyr_ble_timer.h (included above)

// Tick type (used for timing functions)
typedef uint32_t k_ticks_t;

#define K_NO_WAIT ((k_timeout_t) { .ticks = 0 })
#define K_FOREVER ((k_timeout_t) { .ticks = 0xFFFFFFFF })
#define K_MSEC(ms) ((k_timeout_t) { .ticks = (ms) })
#define K_SECONDS(s) K_MSEC((s) * 1000)

// Static work initialization macro (without 'static' - caller adds it)
#define K_WORK_DEFINE(name, work_handler) \
    struct k_work name = { \
        .handler = work_handler, \
        .user_data = NULL, \
        .pending = false, \
        .next = NULL, \
        .prev = NULL \
    }

// Internal work initializer (used by Zephyr code)
#define Z_WORK_INITIALIZER(work_handler) { \
        .handler = work_handler, \
        .user_data = NULL, \
        .pending = false, \
        .next = NULL, \
        .prev = NULL \
    }

// Work status flags (used with k_work_delayable_busy_get)
#define K_WORK_QUEUED    BIT(0)
#define K_WORK_DELAYED   BIT(1)
#define K_WORK_RUNNING   BIT(2)
#define K_WORK_CANCELING BIT(3)

// Timeout conversion helper
static inline uint32_t k_timeout_to_ms(k_timeout_t timeout) {
    return timeout.ticks;
}

// Work queue configuration
struct k_work_queue_config {
    const char *name;
    bool no_yield;      // Don't yield after processing work items
    bool essential;     // Essential work queue (higher priority)
};

// Work synchronization structure (used for k_work_cancel_sync)
struct k_work_sync {
    int dummy;  // Placeholder
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

// Get delayable work from work (for internal use)
// work is embedded in the delayable work structure
static inline struct k_work_delayable *k_work_delayable_from_work(struct k_work *work) {
    return CONTAINER_OF(work, struct k_work_delayable, work);
}

// Get thread from work queue
// Returns &queue->thread so k_current_get() == k_work_queue_thread_get(queue)
// comparison works correctly when in work queue context.
static inline void *k_work_queue_thread_get(struct k_work_q *queue) {
    return &queue->thread;
}

// Flush work (wait for completion) - no-op in MicroPython
static inline int k_work_flush(struct k_work *work, void *sync) {
    (void)work;
    (void)sync;
    return 0;
}

#endif // __ZEPHYR__

// --- MicroPython-specific functions ---

// Waiting flag: When true, allows work processing from within wait loops
// This prevents deadlock when waiting for HCI responses that arrive via work queue
// Set by k_sem_take() during its wait loop
extern volatile bool mp_bluetooth_zephyr_in_wait_loop;

// HCI event processing depth: When > 0, prevents work_process from k_sem_take()
// This prevents re-entrancy where tx_work runs during k_sem_take() in process_pending_cmd()
// Incremented by run_zephyr_hci_task() during post-recv_cb work processing
// Uses counter (not bool) to support nested calls correctly
extern volatile int mp_bluetooth_zephyr_hci_processing_depth;

// Called by MicroPython scheduler to process all pending work (regular work queues only)
void mp_bluetooth_zephyr_work_process(void);

// Called by mp_bluetooth_init() wait loop to process initialization work synchronously
// This processes only the init work queue and has a separate recursion guard
void mp_bluetooth_zephyr_work_process_init(void);

// Init phase control - used to process work synchronously during bt_enable()
void mp_bluetooth_zephyr_init_phase_enter(void);  // Call before bt_enable()
void mp_bluetooth_zephyr_init_phase_exit(void);   // Call after bt_enable() completes
bool mp_bluetooth_zephyr_in_init_phase(void);     // Check if in init phase

// Check if initialization work is pending
// Returns true if init work is available in the work queue
bool mp_bluetooth_zephyr_init_work_pending(void);

// Get and dequeue init work without executing it
// Returns NULL if no work available
// Caller must execute work->handler(work) in main loop context to allow yielding
struct k_work *mp_bluetooth_zephyr_init_work_get(void);

// Debug function to report work processing statistics
void mp_bluetooth_zephyr_work_debug_stats(void);

// BLE Work Queue Thread Control (Phase 3)
// These start/stop the dedicated FreeRTOS thread for processing work queues
// On non-FreeRTOS builds, these are no-ops and polling is used instead
void mp_bluetooth_zephyr_work_thread_start(void);
void mp_bluetooth_zephyr_work_thread_stop(void);

// Drain any pending work items before shutdown
// Called from mp_bluetooth_deinit() before stopping work thread
// Returns true if any work was processed
bool mp_bluetooth_zephyr_work_drain(void);

// Reset work queue state for clean re-initialization
// Called from mp_bluetooth_deinit() to clear stale queue linkages
void mp_bluetooth_zephyr_work_reset(void);

#endif // MICROPY_INCLUDED_EXTMOD_ZEPHYR_BLE_HAL_ZEPHYR_BLE_WORK_H
