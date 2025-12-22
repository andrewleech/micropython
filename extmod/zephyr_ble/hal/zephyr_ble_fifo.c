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
#include "zephyr_ble_fifo.h"
#include "zephyr_ble_work.h"
#include "zephyr_ble_atomic.h"

// Debug flag (disabled for FreeRTOS - mp_printf not thread-safe in work thread)
static volatile int debug_enabled = 0;  // Disable debug to prevent NLR crashes

#define DEBUG_FIFO(fmt, ...) \
    do { \
        if (debug_enabled) { \
            mp_printf(&mp_plat_print, "[FIFO] " fmt "\n", ##__VA_ARGS__); \
        } \
    } while (0)

// sys_snode_t is defined in <zephyr/sys/slist.h>
// Items placed in the FIFO must have sys_snode_t as their first field

// Helper to get snode from item
static inline sys_snode_t *item_to_snode(void *item) {
    return (sys_snode_t *)item;
}

// --- k_queue API Implementation (underlying FIFO implementation) ---

// Enable debug output (called after boot to avoid interfering with initialization)
void mp_bluetooth_zephyr_fifo_enable_debug(void) {
    debug_enabled = 1;
    mp_printf(&mp_plat_print, "[FIFO] Debug output enabled\n");
}

void k_queue_init(struct k_queue *queue) {
    DEBUG_FIFO("k_queue_init(%p)", queue);
    queue->data_q.head = NULL;
    queue->data_q.tail = NULL;
    queue->lock.unused = 0;  // Initialize spinlock for completeness
}

void k_queue_append(struct k_queue *queue, void *data) {
    sys_snode_t *node = item_to_snode(data);

    DEBUG_FIFO("k_queue_append(%p, %p)", queue, data);

    MICROPY_PY_BLUETOOTH_ENTER

    node->next = NULL;

    if (queue->data_q.tail == NULL) {
        // Empty queue
        queue->data_q.head = data;
        queue->data_q.tail = data;
        DEBUG_FIFO("  -> empty queue, set head=tail=%p", data);
    } else {
        // Append to tail
        sys_snode_t *tail_node = item_to_snode(queue->data_q.tail);
        tail_node->next = node;
        queue->data_q.tail = data;
        DEBUG_FIFO("  -> append to tail, new tail=%p", data);
    }

    MICROPY_PY_BLUETOOTH_EXIT
}

void k_queue_prepend(struct k_queue *queue, void *data) {
    sys_snode_t *node = item_to_snode(data);

    DEBUG_FIFO("k_queue_prepend(%p, %p)", queue, data);

    MICROPY_PY_BLUETOOTH_ENTER

    node->next = queue->data_q.head;
    queue->data_q.head = data;

    if (queue->data_q.tail == NULL) {
        // Queue was empty
        queue->data_q.tail = data;
        DEBUG_FIFO("  -> empty queue, set head=tail=%p", data);
    } else {
        DEBUG_FIFO("  -> prepend to head, new head=%p", data);
    }

    MICROPY_PY_BLUETOOTH_EXIT
}

void *k_queue_get(struct k_queue *queue, k_timeout_t timeout) {
    void *item = NULL;

    DEBUG_FIFO("k_queue_get(%p, timeout=%u)", queue, (unsigned)timeout.ticks);

    // Fast path: check if item available
    MICROPY_PY_BLUETOOTH_ENTER
    if (queue->data_q.head != NULL) {
        item = queue->data_q.head;
        sys_snode_t *node = item_to_snode(item);
        queue->data_q.head = node->next;

        if (queue->data_q.head == NULL) {
            // Queue is now empty
            queue->data_q.tail = NULL;
        }

        node->next = NULL;
        MICROPY_PY_BLUETOOTH_EXIT
        DEBUG_FIFO("  -> fast path: return %p", item);
        return item;
    }
    MICROPY_PY_BLUETOOTH_EXIT

    // K_NO_WAIT: Don't block
    if (timeout.ticks == 0) {
        DEBUG_FIFO("  -> K_NO_WAIT: return NULL");
        return NULL;
    }

    // K_FOREVER or timeout: Busy-wait pattern
    // (similar to k_sem_take implementation)
    DEBUG_FIFO("  -> busy-wait mode (timeout=%u)", (unsigned)timeout.ticks);
    uint32_t t0 = mp_hal_ticks_ms();
    uint32_t timeout_ms = (timeout.ticks == 0xFFFFFFFF) ? 0xFFFFFFFF : timeout.ticks;

    int loop_count = 0;
    while (true) {
        // Check if item available
        MICROPY_PY_BLUETOOTH_ENTER
        if (queue->data_q.head != NULL) {
            item = queue->data_q.head;
            sys_snode_t *node = item_to_snode(item);
            queue->data_q.head = node->next;

            if (queue->data_q.head == NULL) {
                queue->data_q.tail = NULL;
            }

            node->next = NULL;
            MICROPY_PY_BLUETOOTH_EXIT
            DEBUG_FIFO("  -> busy-wait: return %p (after %d loops)", item, loop_count);
            return item;
        }
        MICROPY_PY_BLUETOOTH_EXIT

        // Check timeout
        uint32_t elapsed = mp_hal_ticks_ms() - t0;
        if (timeout_ms != 0xFFFFFFFF && elapsed >= timeout_ms) {
            DEBUG_FIFO("  -> timeout after %u ms", elapsed);
            return NULL;
        }

        // Process pending work to allow items to be added
        mp_bluetooth_zephyr_work_process();

        // Wait for events with proper background processing
        // This allows IRQ handlers (UART, timers, etc) to run and add items to queue
        if (timeout_ms == 0xFFFFFFFF) {
            // K_FOREVER: wait indefinitely
            mp_event_wait_indefinite();
        } else {
            // Timed wait: wait for remaining time or until interrupt
            uint32_t remaining = timeout_ms - elapsed;
            if (remaining > 0) {
                mp_event_wait_ms(remaining);
            }
        }

        loop_count++;
        if (loop_count % 100 == 0) {
            DEBUG_FIFO("  -> still waiting (loop %d)", loop_count);
        }
    }
}

// --- k_fifo API Implementation (FIFO = First In First Out) ---

// Note: k_fifo_init() is defined inline in kernel.h

void k_fifo_put(struct k_fifo *fifo, void *data) {
    DEBUG_FIFO("k_fifo_put(%p, %p)", fifo, data);
    // FIFO puts items at the end (append)
    k_queue_append(&fifo->_queue, data);
}

void *k_fifo_get(struct k_fifo *fifo, k_timeout_t timeout) {
    DEBUG_FIFO("k_fifo_get(%p, timeout=%u)", fifo, (unsigned)timeout.ticks);
    // FIFO gets items from the head (same as queue)
    return k_queue_get(&fifo->_queue, timeout);
}

// --- k_lifo API Implementation (LIFO = Last In First Out) ---

// Note: k_lifo_init() is defined inline in kernel.h

void k_lifo_put(struct k_lifo *lifo, void *data) {
    DEBUG_FIFO("k_lifo_put(%p, %p)", lifo, data);
    // LIFO puts items at the front (prepend)
    k_queue_prepend(&lifo->_queue, data);
}

void *k_lifo_get(struct k_lifo *lifo, k_timeout_t timeout) {
    // Access sys_slist_t data_q in the k_queue structure
    // kernel.h defines: struct k_queue { sys_slist_t data_q; struct k_spinlock lock; }
    // sys_slist_t is: struct { sys_snode_t *head; sys_snode_t *tail; }

    void *item = NULL;

    DEBUG_FIFO("k_lifo_get(%p, timeout=%u)", lifo, (unsigned)timeout.ticks);

    // Fast path: check if item available
    MICROPY_PY_BLUETOOTH_ENTER
    void *head = lifo->_queue.data_q.head;
    if (head != NULL) {
        // Remove from head
        sys_snode_t *node = (sys_snode_t *)head;
        lifo->_queue.data_q.head = node->next;

        if (lifo->_queue.data_q.head == NULL) {
            // List is now empty
            lifo->_queue.data_q.tail = NULL;
        }

        node->next = NULL;
        item = head;
        MICROPY_PY_BLUETOOTH_EXIT
        DEBUG_FIFO("  -> fast path: got %p", item);
        return item;
    }
    MICROPY_PY_BLUETOOTH_EXIT

    // K_NO_WAIT: Don't block
    if (timeout.ticks == 0) {
        DEBUG_FIFO("  -> K_NO_WAIT: return NULL");
        return NULL;
    }

    // K_FOREVER or timeout: Busy-wait pattern
    DEBUG_FIFO("  -> busy-wait mode (timeout=%u)", (unsigned)timeout.ticks);
    uint32_t t0 = mp_hal_ticks_ms();
    uint32_t timeout_ms = (timeout.ticks == 0xFFFFFFFF) ? 0xFFFFFFFF : timeout.ticks;

    int loop_count = 0;
    while (true) {
        // Check if item available
        MICROPY_PY_BLUETOOTH_ENTER
        head = lifo->_queue.data_q.head;
        if (head != NULL) {
            // Remove from head
            sys_snode_t *node = (sys_snode_t *)head;
            lifo->_queue.data_q.head = node->next;

            if (lifo->_queue.data_q.head == NULL) {
                lifo->_queue.data_q.tail = NULL;
            }

            node->next = NULL;
            item = head;
            MICROPY_PY_BLUETOOTH_EXIT
            DEBUG_FIFO("  -> busy-wait: got %p (after %d loops)", item, loop_count);
            return item;
        }
        MICROPY_PY_BLUETOOTH_EXIT

        // Check timeout
        uint32_t elapsed = mp_hal_ticks_ms() - t0;
        if (timeout_ms != 0xFFFFFFFF && elapsed >= timeout_ms) {
            DEBUG_FIFO("  -> timeout after %u ms", elapsed);
            return NULL;
        }

        // Process pending work to allow items to be added
        mp_bluetooth_zephyr_work_process();

        // Wait for events with proper background processing
        if (timeout_ms == 0xFFFFFFFF) {
            // K_FOREVER: wait indefinitely
            mp_event_wait_indefinite();
        } else {
            // Timed wait: wait for remaining time or until interrupt
            uint32_t remaining = timeout_ms - elapsed;
            if (remaining > 0) {
                mp_event_wait_ms(remaining);
            }
        }

        loop_count++;
        if (loop_count % 100 == 0) {
            DEBUG_FIFO("  -> still waiting (loop %d)", loop_count);
        }
    }
}
