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
#include "zephyr_ble_fifo.h"
#include "zephyr_ble_work.h"
#include "zephyr_ble_atomic.h"

// Debug flag (enabled after first successful print to avoid boot issues)
static volatile int debug_enabled = 0;

#define DEBUG_FIFO(fmt, ...) \
    do { \
        if (debug_enabled) { \
            mp_printf(&mp_plat_print, "[FIFO] " fmt "\n", ##__VA_ARGS__); \
        } \
    } while (0)

// sys_snode_t structure (first field in items placed in FIFO)
// Items in the FIFO must have this as their first field
struct sys_snode {
    struct sys_snode *next;
};

// Helper to get snode from item
static inline struct sys_snode *item_to_snode(void *item) {
    return (struct sys_snode *)item;
}

// --- k_queue API Implementation (underlying FIFO implementation) ---

// Enable debug output (called after boot to avoid interfering with initialization)
void mp_bluetooth_zephyr_fifo_enable_debug(void) {
    debug_enabled = 1;
    mp_printf(&mp_plat_print, "[FIFO] Debug output enabled\n");
}

void k_queue_init(struct k_queue *queue) {
    DEBUG_FIFO("k_queue_init(%p)", queue);
    queue->head = NULL;
    queue->tail = NULL;
}

void k_queue_append(struct k_queue *queue, void *data) {
    struct sys_snode *node = item_to_snode(data);

    DEBUG_FIFO("k_queue_append(%p, %p)", queue, data);

    MICROPY_PY_BLUETOOTH_ENTER

    node->next = NULL;

    if (queue->tail == NULL) {
        // Empty queue
        queue->head = data;
        queue->tail = data;
        DEBUG_FIFO("  -> empty queue, set head=tail=%p", data);
    } else {
        // Append to tail
        struct sys_snode *tail_node = item_to_snode(queue->tail);
        tail_node->next = node;
        queue->tail = data;
        DEBUG_FIFO("  -> append to tail, new tail=%p", data);
    }

    MICROPY_PY_BLUETOOTH_EXIT
}

void k_queue_prepend(struct k_queue *queue, void *data) {
    struct sys_snode *node = item_to_snode(data);

    DEBUG_FIFO("k_queue_prepend(%p, %p)", queue, data);

    MICROPY_PY_BLUETOOTH_ENTER

    node->next = queue->head;
    queue->head = data;

    if (queue->tail == NULL) {
        // Queue was empty
        queue->tail = data;
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
    if (queue->head != NULL) {
        item = queue->head;
        struct sys_snode *node = item_to_snode(item);
        queue->head = node->next;

        if (queue->head == NULL) {
            // Queue is now empty
            queue->tail = NULL;
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
        if (queue->head != NULL) {
            item = queue->head;
            struct sys_snode *node = item_to_snode(item);
            queue->head = node->next;

            if (queue->head == NULL) {
                queue->tail = NULL;
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

        // Yield to prevent tight loop
        #ifdef MICROPY_EVENT_POLL_HOOK
        MICROPY_EVENT_POLL_HOOK
        #endif

        loop_count++;
        if (loop_count % 100 == 0) {
            DEBUG_FIFO("  -> still waiting (loop %d)", loop_count);
        }
    }
}

// --- k_lifo API Implementation (LIFO = Last In First Out) ---

void k_lifo_put(struct k_lifo *lifo, void *data) {
    DEBUG_FIFO("k_lifo_put(%p, %p)", lifo, data);
    // LIFO puts items at the front (prepend)
    k_queue_prepend(&lifo->_queue, data);
}
