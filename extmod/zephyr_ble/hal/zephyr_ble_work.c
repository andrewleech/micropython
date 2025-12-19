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
#include "zephyr_ble_atomic.h"

#include <stddef.h>

#if MICROPY_PY_THREAD
#include "FreeRTOS.h"
#include "task.h"
#include "semphr.h"
#include "timers.h"
#include "extmod/freertos/mp_freertos_service.h"

// BLE Work Queue Thread Configuration
#define BLE_WORK_THREAD_STACK_SIZE (4096 / sizeof(StackType_t))  // 4KB stack
#define BLE_WORK_THREAD_PRIORITY   (configMAX_PRIORITIES - 2)    // High priority

// Static allocation for work queue thread
static StaticTask_t ble_work_thread_tcb;
static StackType_t ble_work_thread_stack[BLE_WORK_THREAD_STACK_SIZE];
static TaskHandle_t ble_work_thread_handle = NULL;

// Binary semaphore to signal work available
static StaticSemaphore_t ble_work_sem_storage;
static SemaphoreHandle_t ble_work_sem = NULL;

// Flag to signal thread shutdown
static volatile bool ble_work_thread_running = false;

// Forward declaration
static void ble_work_thread_func(void *param);
#endif

// Forward declare bt_dev to detect initialization work
// bt_dev is defined in Zephyr's hci_core.c
struct bt_dev {
    struct k_work init;
    // ... other fields not relevant for detection
};
extern struct bt_dev bt_dev;

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

#if MICROPY_PY_THREAD
    // Signal the work queue thread (ISR-safe)
    if (ble_work_sem != NULL) {
        if (mp_freertos_service_in_isr()) {
            // ISR context - use ISR-safe API
            BaseType_t xHigherPriorityTaskWoken = pdFALSE;
            xSemaphoreGiveFromISR(ble_work_sem, &xHigherPriorityTaskWoken);
            portYIELD_FROM_ISR(xHigherPriorityTaskWoken);
        } else {
            // Task context - use normal API
            xSemaphoreGive(ble_work_sem);
        }
    }
#endif

    return 1;
}

int k_work_submit(struct k_work *work) {
    // Route bt_dev.init to initialization queue
    // This allows synchronous processing from mp_bluetooth_init() wait loop
    if (work == &bt_dev.init) {
        if (!k_init_work_q.head && !k_init_work_q.nextq) {
            k_work_queue_init(&k_init_work_q);
            k_init_work_q.name = "INIT WQ";
        }
        return k_work_submit_internal(&k_init_work_q, work);
    }

    // All other work goes to system work queue
    if (!k_sys_work_q.head && !k_sys_work_q.nextq) {
        k_work_queue_init(&k_sys_work_q);
        k_sys_work_q.name = "SYS WQ";
    }
    int ret = k_work_submit_internal(&k_sys_work_q, work);

    // ARCHITECTURAL FIX: Trigger work processing immediately after submission
    // In a real Zephyr system with threads, the worker thread would wake up.
    // In our system, we need to schedule run_zephyr_hci_task() to process the work.
    if (ret > 0) {
        extern void mp_bluetooth_zephyr_port_poll_in_ms(uint32_t ms);
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
    if (!k_sys_work_q.head && !k_sys_work_q.nextq) {
        k_work_queue_init(&k_sys_work_q);
        k_sys_work_q.name = "SYS WQ";
    }
    return k_work_schedule_for_queue(&k_sys_work_q, dwork, delay);
}

int k_work_reschedule_for_queue(struct k_work_q *queue, struct k_work_delayable *dwork, k_timeout_t delay) {
    DEBUG_WORK_printf("k_work_reschedule_for_queue(%p, %p, %u)\n", queue, dwork, delay.ticks);

    // Reschedule is the same as schedule in our implementation
    return k_work_schedule_for_queue(queue, dwork, delay);
}

int k_work_reschedule(struct k_work_delayable *dwork, k_timeout_t delay) {
    if (!k_sys_work_q.head && !k_sys_work_q.nextq) {
        k_work_queue_init(&k_sys_work_q);
        k_sys_work_q.name = "SYS WQ";
    }
    return k_work_reschedule_for_queue(&k_sys_work_q, dwork, delay);
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
#if MICROPY_PY_THREAD
    // FreeRTOS: Check if timer is active using FreeRTOS API
    if (dwork->timer.handle == NULL || !xTimerIsTimerActive(dwork->timer.handle)) {
        return 0;
    }
    // FreeRTOS doesn't easily expose remaining time, return 1 if active
    return 1;
#else
    // Polling: Use the active flag and expiry_ticks
    if (!dwork->timer.active) {
        return 0;
    }

    uint32_t now = mp_hal_ticks_ms();
    if (dwork->timer.expiry_ticks > now) {
        return dwork->timer.expiry_ticks - now;
    }
    return 0;
#endif
}

bool k_work_delayable_is_pending(const struct k_work_delayable *dwork) {
#if MICROPY_PY_THREAD
    // FreeRTOS: Check work pending flag and timer active state
    bool timer_active = (dwork->timer.handle != NULL) && xTimerIsTimerActive(dwork->timer.handle);
    return dwork->work.pending || timer_active;
#else
    // Polling: Use the active flag
    return dwork->work.pending || dwork->timer.active;
#endif
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

// Debug counters for work processing investigation
static int work_process_call_count = 0;
static int work_items_processed = 0;

// Called by mp_bluetooth_hci_poll() to process all pending work (regular work queues only)
void mp_bluetooth_zephyr_work_process(void) {
    work_process_call_count++;

#if MICROPY_PY_THREAD
    // On FreeRTOS, the dedicated work thread handles work processing.
    // Skip polling-based processing when thread is active to avoid redundant work.
    // Check ble_work_sem as the authoritative indicator - it's cleared first during shutdown,
    // ensuring no race where both thread and polling process work simultaneously.
    if (ble_work_sem != NULL) {
        DEBUG_WORK_printf("work_process: skipping (work thread active)\n");
        return;
    }
#endif

    // ARCHITECTURAL FIX for Issue #6 recursion deadlock:
    // Prevent recursion UNLESS we're explicitly in a wait loop.
    // When mp_bluetooth_zephyr_in_wait_loop is true, we MUST allow work processing
    // because k_sem_take() is waiting for an HCI response that will arrive via work queue.
    // Without this exception, the recursion prevention creates a deadlock.
    if (regular_work_processor_running && !mp_bluetooth_zephyr_in_wait_loop) {
        DEBUG_WORK_printf("work_process: already running, skipping (recursion prevention)\n");
        return;
    }

    if (regular_work_processor_running && mp_bluetooth_zephyr_in_wait_loop) {
        DEBUG_WORK_printf("work_process: allowing re-entry due to wait loop context\n");
    }

    regular_work_processor_running = true;

    // Process all work queues EXCEPT k_init_work_q (similar to NimBLE's eventq_run_all)
    for (struct k_work_q *q = global_work_q; q != NULL; q = q->nextq) {
        // Skip initialization work queue (processed separately by mp_bluetooth_zephyr_work_process_init)
        if (q == &k_init_work_q) {
            continue;
        }

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
            DEBUG_WORK_printf("work_execute(%p, handler=%p) [total: %d]\n", work, work->handler, work_items_processed);
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

    regular_work_processor_running = false;
}

// Called by mp_bluetooth_init() wait loop to process initialization work synchronously
void mp_bluetooth_zephyr_work_process_init(void) {
    // Prevent recursion (though unlikely since init work should be synchronous)
    if (init_work_processor_running) {
        DEBUG_WORK_printf("init_work_process: already running, skipping\n");
        return;
    }

    init_work_processor_running = true;

    // Process ONLY the initialization work queue
    struct k_work_q *q = &k_init_work_q;

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
        DEBUG_WORK_printf("init_work_execute(%p, handler=%p)\n", work, work->handler);
        if (work->handler) {
            work->handler(work);
        }
        DEBUG_WORK_printf("init_work_execute(%p) done\n", work);

        // Check if work was re-enqueued during execution
        if (work->pending) {
            DEBUG_WORK_printf("  --> init work re-enqueued, stopping queue processing\n");
            break;
        }
    }

    init_work_processor_running = false;
}

// --- Init Work Helper Functions ---

// Check if initialization work is pending in the init work queue
// Called from mp_bluetooth_init() wait loop to check if init work is available
bool mp_bluetooth_zephyr_init_work_pending(void) {
    MICROPY_PY_BLUETOOTH_ENTER
    bool pending = (k_init_work_q.head != NULL);
    MICROPY_PY_BLUETOOTH_EXIT
    return pending;
}

// Get and dequeue init work without executing it
// Returns NULL if no work available
// Caller must execute work->handler(work) in main loop context
// This allows the handler to yield via k_sem_take() → mp_event_wait_*()
struct k_work *mp_bluetooth_zephyr_init_work_get(void) {
    MICROPY_PY_BLUETOOTH_ENTER

    struct k_work *work = k_init_work_q.head;
    if (work == NULL) {
        MICROPY_PY_BLUETOOTH_EXIT
        return NULL;
    }

    // Dequeue work item (remove from queue, mark as not pending)
    k_init_work_q.head = work->next;
    if (k_init_work_q.head) {
        k_init_work_q.head->prev = NULL;
    }

    work->next = NULL;
    work->prev = NULL;
    work->pending = false;

    DEBUG_WORK_printf("init_work_get: dequeued work=%p, handler=%p\n", work, work->handler);

    MICROPY_PY_BLUETOOTH_EXIT
    return work;
}

// Debug function to report work processing statistics
void mp_bluetooth_zephyr_work_debug_stats(void) {
    mp_printf(&mp_plat_print, "WORK STATS: process() called %d times, %d items processed\n",
        work_process_call_count, work_items_processed);
}

// ============================================================================
// FreeRTOS Work Queue Thread (Phase 3)
// ============================================================================
#if MICROPY_PY_THREAD

// Process all pending work in all queues (except init queue)
// Called by the work queue thread
static void ble_work_process_all(void) {
    for (struct k_work_q *q = global_work_q; q != NULL; q = q->nextq) {
        // Skip initialization work queue (processed separately)
        if (q == &k_init_work_q) {
            continue;
        }

        // Process all work items in this queue
        // Note: Must check q->head inside critical section to avoid race
        for (;;) {
            // Dequeue work item atomically
            MICROPY_PY_BLUETOOTH_ENTER
            struct k_work *work = q->head;
            if (work == NULL) {
                MICROPY_PY_BLUETOOTH_EXIT
                break;  // Queue empty
            }
            q->head = work->next;
            if (q->head) {
                q->head->prev = NULL;
            }
            work->next = NULL;
            work->prev = NULL;
            work->pending = false;
            MICROPY_PY_BLUETOOTH_EXIT

            // Execute work handler outside critical section
            work_items_processed++;
            DEBUG_WORK_printf("work_thread: execute(%p, handler=%p) [total: %d]\n",
                work, work->handler, work_items_processed);

            if (work->handler) {
                work->handler(work);
            }

            DEBUG_WORK_printf("work_thread: execute(%p) done\n", work);
        }
    }
}

// BLE Work Queue Thread Function
// Blocks on semaphore waiting for work, processes work when signaled
static void ble_work_thread_func(void *param) {
    (void)param;

    DEBUG_WORK_printf("work_thread: started\n");

    while (ble_work_thread_running) {
        // Block waiting for work (with timeout to check shutdown flag)
        if (xSemaphoreTake(ble_work_sem, pdMS_TO_TICKS(100)) == pdTRUE) {
            // Process all pending work
            ble_work_process_all();
        }
        // Timeout: loop back to check ble_work_thread_running flag
    }

    DEBUG_WORK_printf("work_thread: exiting\n");
    vTaskDelete(NULL);
}

// Start the BLE work queue thread
// Called from mp_bluetooth_init() after basic initialization
void mp_bluetooth_zephyr_work_thread_start(void) {
    if (ble_work_thread_handle != NULL) {
        DEBUG_WORK_printf("work_thread: already running\n");
        return;
    }

    DEBUG_WORK_printf("work_thread: starting...\n");

    // Create binary semaphore for work signaling
    ble_work_sem = xSemaphoreCreateBinaryStatic(&ble_work_sem_storage);

    // Create the work queue thread
    ble_work_thread_running = true;
    ble_work_thread_handle = xTaskCreateStatic(
        ble_work_thread_func,
        "BLE_WORK",
        BLE_WORK_THREAD_STACK_SIZE,
        NULL,
        BLE_WORK_THREAD_PRIORITY,
        ble_work_thread_stack,
        &ble_work_thread_tcb
    );

    DEBUG_WORK_printf("work_thread: started, handle=%p\n", ble_work_thread_handle);
}

// Stop the BLE work queue thread
// Called from mp_bluetooth_deinit() before shutdown
void mp_bluetooth_zephyr_work_thread_stop(void) {
    if (ble_work_thread_handle == NULL) {
        return;
    }

    DEBUG_WORK_printf("work_thread: stopping...\n");

    // Signal thread to exit - this also prevents polling from processing work
    ble_work_thread_running = false;

    // Save handle before clearing (for waiting)
    TaskHandle_t thread_to_wait = ble_work_thread_handle;
    SemaphoreHandle_t sem_to_signal = ble_work_sem;

    // Clear handles first to prevent new work from signaling
    ble_work_thread_handle = NULL;
    ble_work_sem = NULL;

    // Wake the thread so it can check the flag and exit
    if (sem_to_signal != NULL) {
        xSemaphoreGive(sem_to_signal);
    }

    // Wait for thread to actually exit with timeout
    // The thread calls vTaskDelete(NULL) which transitions to deleted state
    // We poll the task state to confirm deletion
    if (thread_to_wait != NULL) {
        for (int i = 0; i < 50; i++) {  // 50 * 10ms = 500ms max wait
            eTaskState state = eTaskGetState(thread_to_wait);
            if (state == eDeleted || state == eInvalid) {
                break;
            }
            vTaskDelay(pdMS_TO_TICKS(10));
        }
    }

    DEBUG_WORK_printf("work_thread: stopped\n");
}

#else // !MICROPY_PY_THREAD

// Stub implementations for non-FreeRTOS builds
void mp_bluetooth_zephyr_work_thread_start(void) {
    // No-op: polling-based work processing used instead
}

void mp_bluetooth_zephyr_work_thread_stop(void) {
    // No-op
}

#endif // MICROPY_PY_THREAD
