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

#include "py/mpconfig.h"
#include "py/mphal.h"
#include "py/runtime.h"
#include "zephyr_ble_work.h"
#include "zephyr_ble_port.h"
#include "zephyr_ble_atomic.h"

#include <stddef.h>


// Flag to indicate we're in the bt_enable() init phase
// During this phase, mp_bluetooth_zephyr_init_work_get() will get work from k_sys_work_q
static volatile bool in_bt_enable_init = false;

// Flag to indicate we're currently executing work from k_sys_work_q
// Used by k_current_get() to return &k_sys_work_q.thread when in work context
static volatile bool in_sys_work_q_context = false;

// Check if currently executing work from system work queue
// Used by k_current_get() to properly identify work queue thread context
bool mp_bluetooth_zephyr_in_sys_work_q_context(void) {
    return in_sys_work_q_context;
}

// Set the system work queue context flag
// Call with true before executing work from k_sys_work_q, false after
void mp_bluetooth_zephyr_set_sys_work_q_context(bool in_context) {
    in_sys_work_q_context = in_context;
}

// Use CONTAINER_OF from zephyr_ble_work.h (already defined there)

#if ZEPHYR_BLE_DEBUG
#define DEBUG_WORK_printf(...) mp_printf(&mp_plat_print, "WORK: " __VA_ARGS__)
#else
#define DEBUG_WORK_printf(...) do {} while (0)
#endif

// Global linked list of work queues (similar to NimBLE's global_eventq)
static struct k_work_q *global_work_q = NULL;

// Default system work queue (Zephyr's k_sys_work_q)
// Must be non-static to be accessible from Zephyr BLE host code
struct k_work_q k_sys_work_q = {0};

// Ensure system work queue is initialized (lazy, idempotent).
static void ensure_sys_work_q_init(void) {
    if (!k_sys_work_q.head && !k_sys_work_q.nextq) {
        k_work_queue_init(&k_sys_work_q);
        k_sys_work_q.name = "SYS WQ";
    }
}

// Initialization work queue (for bt_dev.init work only)
// Processed synchronously from mp_bluetooth_init() wait loop
static struct k_work_q k_init_work_q = {0};

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
    // All work goes to system work queue
    // During bt_enable() init phase, mp_bluetooth_zephyr_init_work_get() reads from here
    DEBUG_WORK_printf("k_work_submit: work=%p\n", work);
    if (work == NULL) {
        DEBUG_WORK_printf("  ERROR: work is NULL!\n");
        return -22;  // EINVAL
    }
    DEBUG_WORK_printf("  handler=%p, pending=%d\n", work->handler, work->pending);
    ensure_sys_work_q_init();
    int ret = k_work_submit_internal(&k_sys_work_q, work);

    // ARCHITECTURAL FIX: Trigger work processing immediately after submission
    // In a real Zephyr system with threads, the worker thread would wake up.
    // In our system, we need to schedule run_zephyr_hci_task() to process the work.
    // EXCEPTION: During init phase, don't trigger immediate processing.
    //            Init work will be manually retrieved and executed by mp_bluetooth_init() wait loop.
    if (ret > 0 && !mp_bluetooth_zephyr_in_init_phase()) {
        mp_bluetooth_zephyr_port_poll_in_ms(0);  // Schedule immediate processing
    }

    return ret;
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
    ensure_sys_work_q_init();
    return k_work_schedule_for_queue(&k_sys_work_q, dwork, delay);
}

int k_work_reschedule(struct k_work_delayable *dwork, k_timeout_t delay) {
    ensure_sys_work_q_init();
    return k_work_schedule_for_queue(&k_sys_work_q, dwork, delay);
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

// Recursion guards for work processing
static volatile bool regular_work_processor_running = false;
static volatile bool init_work_processor_running = false;

// Waiting flag: When true, allows work processing from within wait loops
// This prevents deadlock when waiting for HCI responses that arrive via work queue
// Set by k_sem_take() during its wait loop (exported in zephyr_ble_work.h)
volatile bool mp_bluetooth_zephyr_in_wait_loop = false;

// HCI event processing depth: Used by STM32 port to prevent re-entrancy.
volatile int mp_bluetooth_zephyr_hci_processing_depth = 0;

// Recursion depth counter: bounds the blocking time from stale post-disconnect
// work handlers that call k_sem_take(K_FOREVER) inside work_process recursion.
// Without this, each nesting level adds ZEPHYR_BLE_POLL_MAX_TIMEOUT_MS delay.
static int work_process_depth = 0;

// Debug counters for work processing investigation
static int work_process_call_count = 0;
static int work_items_processed = 0;

// Called by mp_bluetooth_hci_poll() to process all pending work (regular work queues only).
// Returns true if any work items were executed.
bool mp_bluetooth_zephyr_work_process(void) {
    work_process_call_count++;
    DEBUG_WORK_printf("work_process: entered, count=%d\n", work_process_call_count);

    // ARCHITECTURAL FIX for Issue #6 recursion deadlock:
    // Prevent recursion UNLESS we're explicitly in a wait loop.
    // When mp_bluetooth_zephyr_in_wait_loop is true, we MUST allow work processing
    // because k_sem_take() is waiting for an HCI response that will arrive via work queue.
    // Without this exception, the recursion prevention creates a deadlock.
    if (regular_work_processor_running && !mp_bluetooth_zephyr_in_wait_loop) {
        return false;
    }

    // Limit recursion depth to bound blocking from stale post-disconnect work.
    // Depth 0→1: Normal poll → work_process.
    // Depth 1→2: k_sem_take → hci_uart_wfi → poll → work_process (needed for HCI responses).
    // Depth 2+: Stale handler → k_sem_take → ... → work_process — blocked to prevent
    //   cascading timeouts where each level adds ZEPHYR_BLE_POLL_MAX_TIMEOUT_MS.
    if (work_process_depth >= 2) {
        return false;
    }

    work_process_depth++;
    regular_work_processor_running = true;
    bool did_work = false;

    // Process all work queues EXCEPT k_init_work_q (similar to NimBLE's eventq_run_all)
    for (struct k_work_q *q = global_work_q; q != NULL; q = q->nextq) {
        // Skip initialization work queue (processed separately by mp_bluetooth_zephyr_work_process_init)
        if (q == &k_init_work_q) {
            continue;
        }

        int iter_count = 0;
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
            work_items_processed++;
            did_work = true;
            if (work->handler) {
                // Set context flag if processing system work queue
                bool is_sys_wq = (q == &k_sys_work_q);
                if (is_sys_wq) {
                    in_sys_work_q_context = true;
                }

                work->handler(work);
                if (is_sys_wq) {
                    in_sys_work_q_context = false;
                }
            }

            iter_count++;
            if (iter_count > 100) {
                break;
            }

            // Note: We intentionally do NOT break if work was re-enqueued.
            // Zephyr's tx_processor re-submits tx_work to continue processing TX data.
            // If we broke here, subsequent work items (like rx_work) wouldn't run,
            // and the re-submitted tx_work wouldn't process until next poll cycle.
            // Let the loop continue naturally - the re-enqueued work will be
            // processed in subsequent iterations.
        }
    }

    regular_work_processor_running = false;
    work_process_depth--;
    return did_work;
}


// --- Init Work Helper Functions ---

// Check if initialization work is pending in the init work queue
// Set init phase flag - call before bt_enable()
void mp_bluetooth_zephyr_init_phase_enter(void) {
    in_bt_enable_init = true;
    DEBUG_WORK_printf("Entering init phase\n");
}

// Clear init phase flag - call after bt_enable() completes
void mp_bluetooth_zephyr_init_phase_exit(void) {
    DEBUG_WORK_printf("Exiting init phase\n");
    in_bt_enable_init = false;
}

// Check if in init phase
bool mp_bluetooth_zephyr_in_init_phase(void) {
    return in_bt_enable_init;
}

// Called from mp_bluetooth_init() wait loop to check if init work is available
bool mp_bluetooth_zephyr_init_work_pending(void) {
    MICROPY_PY_BLUETOOTH_ENTER
    // During init phase, check system work queue
    bool pending = (k_sys_work_q.head != NULL);
    MICROPY_PY_BLUETOOTH_EXIT
    return pending;
}

// Get and dequeue init work without executing it
// Returns NULL if no work available
// Caller must execute work->handler(work) in main loop context
// This allows the handler to yield via k_sem_take() → mp_event_wait_*()
struct k_work *mp_bluetooth_zephyr_init_work_get(void) {
    MICROPY_PY_BLUETOOTH_ENTER

    // During init phase, get work from system work queue
    struct k_work *work = k_sys_work_q.head;
    if (work == NULL) {
        MICROPY_PY_BLUETOOTH_EXIT
        return NULL;
    }

    // Dequeue work item (remove from queue, mark as not pending)
    k_sys_work_q.head = work->next;
    if (k_sys_work_q.head) {
        k_sys_work_q.head->prev = NULL;
    }

    work->next = NULL;
    work->prev = NULL;
    work->pending = false;

    DEBUG_WORK_printf("init_work_get: dequeued work=%p, handler=%p from SYS WQ\n", work, work->handler);

    MICROPY_PY_BLUETOOTH_EXIT
    return work;
}



// Weak stub implementations -- work processed via cooperative polling,
// no dedicated work thread.  Ports with a dedicated HCI RX thread (e.g.
// Unix) override these with strong definitions.
__attribute__((weak))
void mp_bluetooth_zephyr_work_thread_start(void) {
}

__attribute__((weak))
void mp_bluetooth_zephyr_work_thread_stop(void) {
}

// Discard all pending work items without executing their handlers.
// Called before bt_disable() to clear stale post-disconnect work items that
// would otherwise block in k_sem_take() during the bt_disable→k_sem_take→
// hci_uart_wfi→poll→work_process recursion chain.
void mp_bluetooth_zephyr_work_clear_pending(void) {
    MICROPY_PY_BLUETOOTH_ENTER

    for (struct k_work_q *q = global_work_q; q != NULL; q = q->nextq) {
        while (q->head != NULL) {
            struct k_work *work = q->head;
            q->head = work->next;
            work->next = NULL;
            work->prev = NULL;
            work->pending = false;
        }
    }

    MICROPY_PY_BLUETOOTH_EXIT
}

// Drain any pending work items before shutdown
// Called from mp_bluetooth_deinit() before stopping work thread
// This ensures connection events and other pending work is processed before cleanup
bool mp_bluetooth_zephyr_work_drain(void) {
    bool any_work = false;
    mp_uint_t timeout_start = mp_hal_ticks_ms();

    DEBUG_WORK_printf("work_drain: starting\n");

    // Process work for up to 100ms
    while (mp_hal_ticks_ms() - timeout_start < 100) {
        bool found_work = false;

        // Check if any work queue has pending items
        for (struct k_work_q *q = global_work_q; q != NULL; q = q->nextq) {
            // Skip initialization work queue
            if (q == &k_init_work_q) {
                continue;
            }
            if (q->head != NULL) {
                found_work = true;
                break;
            }
        }

        if (!found_work) {
            break;  // No more pending work
        }

        // Process work (this will handle one batch of items)
        mp_bluetooth_zephyr_work_process();
        any_work = true;

        // Brief yield to allow other work to be submitted
        mp_event_wait_ms(1);
    }

    DEBUG_WORK_printf("work_drain: done, processed=%d\n", any_work);
    return any_work;
}

// Reset work queue state for clean re-initialization
// This clears the global work queue list and resets the system work queue
// Called from mp_bluetooth_deinit() to prevent stale queue linkages
void mp_bluetooth_zephyr_work_reset(void) {
    DEBUG_WORK_printf("work_reset: clearing work queue state\n");

    // First, clear pending flags on all work items still in queues.
    // Without this, work items like rx_work retain pending=true after the
    // queue heads are NULLed below. On the next session, k_work_submit()
    // returns 0 ("already pending") without enqueuing, so rx_work_handler
    // never runs and async HCI events (connection complete) are never processed.
    mp_bluetooth_zephyr_work_clear_pending();

    // Clear the global work queue list
    global_work_q = NULL;

    // Reset system work queue
    k_sys_work_q.head = NULL;
    k_sys_work_q.nextq = NULL;
    k_sys_work_q.name = NULL;

    // Reset init work queue
    k_init_work_q.head = NULL;
    k_init_work_q.nextq = NULL;
    k_init_work_q.name = NULL;

    // Reset recursion guards
    regular_work_processor_running = false;
    init_work_processor_running = false;

    // Reset init phase flag
    in_bt_enable_init = false;
    in_sys_work_q_context = false;

    // Reset wait loop flag (could be stuck if test aborted during k_sem_take)
    mp_bluetooth_zephyr_in_wait_loop = false;
    mp_bluetooth_zephyr_hci_processing_depth = 0;

    // Reset recursion depth counter
    work_process_depth = 0;

    // Reset debug counters
    work_process_call_count = 0;
    work_items_processed = 0;
}
