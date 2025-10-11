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
#include "zephyr_ble_work.h"
#include "zephyr_ble_atomic.h"

#include <stddef.h>

// CONTAINER_OF macro (standard container_of pattern)
#define CONTAINER_OF(ptr, type, field) \
    ((type *)(((char *)(ptr)) - offsetof(type, field)))

#define DEBUG_WORK_printf(...) // printf(__VA_ARGS__)

// Global linked list of work queues (similar to NimBLE's global_eventq)
static struct k_work_q *global_work_q = NULL;

// Default system work queue (like Zephyr's k_sys_work_q)
static struct k_work_q system_work_q;

// --- Work Queue Management ---

void k_work_queue_init(struct k_work_q *queue) {
    DEBUG_WORK_printf("k_work_queue_init(%p)\n", queue);
    queue->head = NULL;
    queue->name = NULL;

    // Add to global queue list
    struct k_work_q **q;
    for (q = &global_work_q; *q != NULL; q = &(*q)->nextq) {
        if (*q == queue) {
            // Already in list
            return;
        }
    }
    *q = queue;
    queue->nextq = NULL;
}

void k_work_queue_start(struct k_work_q *queue, void *stack, size_t stack_size, int prio, const struct k_work_queue_config *cfg) {
    DEBUG_WORK_printf("k_work_queue_start(%p, stack=%p, size=%u, prio=%d, cfg=%p)\n",
        queue, stack, (unsigned)stack_size, prio, cfg);

    // In MicroPython, we don't create threads
    // Just initialize the queue and set the name
    if (!queue->head) {
        k_work_queue_init(queue);
    }

    if (cfg && cfg->name) {
        queue->name = cfg->name;
    }

    // Note: stack, stack_size, and prio are ignored in MicroPython implementation
}

// --- Basic Work API ---

void k_work_init(struct k_work *work, k_work_handler_t handler) {
    DEBUG_WORK_printf("k_work_init(%p, %p)\n", work, handler);
    work->handler = handler;
    work->user_data = NULL;
    work->pending = false;
    work->next = NULL;
    work->prev = NULL;
}

static int k_work_submit_internal(struct k_work_q *queue, struct k_work *work) {
    DEBUG_WORK_printf("k_work_submit_internal(%p, %p)\n", queue, work);

    MICROPY_PY_BLUETOOTH_ENTER

    // Check if already pending in any queue
    if (work->pending) {
        DEBUG_WORK_printf("  --> already pending\n");
        MICROPY_PY_BLUETOOTH_EXIT
        return 0;
    }

    // Add to queue's linked list
    work->pending = true;
    work->next = NULL;

    if (queue->head == NULL) {
        // Empty queue
        queue->head = work;
        work->prev = NULL;
    } else {
        // Find tail and append
        struct k_work *tail = queue->head;
        while (tail->next != NULL) {
            tail = tail->next;
        }
        tail->next = work;
        work->prev = tail;
    }

    MICROPY_PY_BLUETOOTH_EXIT
    return 1;
}

int k_work_submit(struct k_work *work) {
    // Submit to system work queue
    if (!system_work_q.head && !system_work_q.nextq) {
        k_work_queue_init(&system_work_q);
        system_work_q.name = "SYS WQ";
    }
    return k_work_submit_internal(&system_work_q, work);
}

int k_work_submit_to_queue(struct k_work_q *queue, struct k_work *work) {
    return k_work_submit_internal(queue, work);
}

int k_work_cancel(struct k_work *work) {
    DEBUG_WORK_printf("k_work_cancel(%p)\n", work);

    MICROPY_PY_BLUETOOTH_ENTER

    if (!work->pending) {
        MICROPY_PY_BLUETOOTH_EXIT
        return 0;
    }

    // Remove from queue
    if (work->prev) {
        work->prev->next = work->next;
    }
    if (work->next) {
        work->next->prev = work->prev;
    }

    // Update queue head if necessary
    for (struct k_work_q *q = global_work_q; q != NULL; q = q->nextq) {
        if (q->head == work) {
            q->head = work->next;
            break;
        }
    }

    work->pending = false;
    work->next = NULL;
    work->prev = NULL;

    MICROPY_PY_BLUETOOTH_EXIT
    return 1;
}

int k_work_cancel_sync(struct k_work *work, void *sync) {
    // sync parameter is for synchronization with other threads, not needed in MicroPython
    (void)sync;
    return k_work_cancel(work);
}

bool k_work_is_pending(const struct k_work *work) {
    return work->pending;
}

// --- Delayable Work API ---

// Timer callback for delayable work
static void delayable_work_timer_fn(struct k_timer *timer) {
    struct k_work_delayable *dwork = CONTAINER_OF(timer, struct k_work_delayable, timer);

    DEBUG_WORK_printf("delayable_work_timer_fn(%p) -> submitting work\n", dwork);

    // Submit the work to its queue
    if (dwork->queue) {
        k_work_submit_to_queue(dwork->queue, &dwork->work);
    } else {
        k_work_submit(&dwork->work);
    }
}

void k_work_init_delayable(struct k_work_delayable *dwork, k_work_handler_t handler) {
    DEBUG_WORK_printf("k_work_init_delayable(%p, %p)\n", dwork, handler);

    k_work_init(&dwork->work, handler);
    k_timer_init(&dwork->timer, delayable_work_timer_fn, NULL);
    dwork->queue = NULL;
}

int k_work_schedule_for_queue(struct k_work_q *queue, struct k_work_delayable *dwork, k_timeout_t delay) {
    DEBUG_WORK_printf("k_work_schedule_for_queue(%p, %p, %u)\n", queue, dwork, delay.ticks);

    // Cancel any pending timer
    k_timer_stop(&dwork->timer);

    // Cancel any pending work
    k_work_cancel(&dwork->work);

    // Set the target queue
    dwork->queue = queue;

    if (delay.ticks == 0) {
        // No delay, submit immediately
        return k_work_submit_to_queue(queue, &dwork->work);
    } else {
        // Start timer to submit later
        k_timer_start(&dwork->timer, delay, K_NO_WAIT);
        return 1;
    }
}

int k_work_schedule(struct k_work_delayable *dwork, k_timeout_t delay) {
    if (!system_work_q.head && !system_work_q.nextq) {
        k_work_queue_init(&system_work_q);
        system_work_q.name = "SYS WQ";
    }
    return k_work_schedule_for_queue(&system_work_q, dwork, delay);
}

int k_work_reschedule_for_queue(struct k_work_q *queue, struct k_work_delayable *dwork, k_timeout_t delay) {
    DEBUG_WORK_printf("k_work_reschedule_for_queue(%p, %p, %u)\n", queue, dwork, delay.ticks);

    // Reschedule is the same as schedule in our implementation
    return k_work_schedule_for_queue(queue, dwork, delay);
}

int k_work_reschedule(struct k_work_delayable *dwork, k_timeout_t delay) {
    if (!system_work_q.head && !system_work_q.nextq) {
        k_work_queue_init(&system_work_q);
        system_work_q.name = "SYS WQ";
    }
    return k_work_reschedule_for_queue(&system_work_q, dwork, delay);
}

int k_work_cancel_delayable(struct k_work_delayable *dwork) {
    DEBUG_WORK_printf("k_work_cancel_delayable(%p)\n", dwork);

    // Stop the timer
    k_timer_stop(&dwork->timer);

    // Cancel the work
    return k_work_cancel(&dwork->work);
}

int k_work_cancel_delayable_sync(struct k_work_delayable *dwork, void *sync) {
    // sync parameter is for synchronization with other threads, not needed
    (void)sync;
    return k_work_cancel_delayable(dwork);
}

k_ticks_t k_work_delayable_remaining_get(const struct k_work_delayable *dwork) {
    if (!dwork->timer.active) {
        return 0;
    }

    uint32_t now = mp_hal_ticks_ms();
    if (dwork->timer.expiry_ticks > now) {
        return dwork->timer.expiry_ticks - now;
    }
    return 0;
}

bool k_work_delayable_is_pending(const struct k_work_delayable *dwork) {
    return dwork->work.pending || dwork->timer.active;
}

int k_work_delayable_busy_get(const struct k_work_delayable *dwork) {
    // Return non-zero if work is pending or timer is active
    return k_work_delayable_is_pending(dwork) ? 1 : 0;
}

// --- MicroPython Integration ---

// Called by mp_bluetooth_hci_poll() to process all pending work
void mp_bluetooth_zephyr_work_process(void) {
    // Process all work queues (similar to NimBLE's eventq_run_all)
    for (struct k_work_q *q = global_work_q; q != NULL; q = q->nextq) {
        while (q->head != NULL) {
            // Dequeue work item
            struct k_work *work = q->head;
            q->head = work->next;

            if (q->head) {
                q->head->prev = NULL;
            }

            work->next = NULL;
            work->prev = NULL;
            work->pending = false;

            // Execute work handler
            DEBUG_WORK_printf("work_execute(%p, handler=%p)\n", work, work->handler);
            if (work->handler) {
                work->handler(work);
            }
            DEBUG_WORK_printf("work_execute(%p) done\n", work);

            // Check if work was re-enqueued during execution
            if (work->pending) {
                DEBUG_WORK_printf("  --> work re-enqueued, stopping queue processing\n");
                break;
            }
        }
    }
}
