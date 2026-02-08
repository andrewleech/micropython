/*
 * This file is part of the MicroPython project, http://micropython.org/
 *
 * SPDX-License-Identifier: Apache-2.0
 *
 * Minimal kernel.h stub for Zephyr BLE without full RTOS
 *
 * This file provides the kernel API needed by Zephyr BLE, implemented
 * by our HAL layer. It does NOT include the real Zephyr kernel.h to
 * avoid pulling in full threading/RTOS infrastructure.
 */

#ifndef MP_ZEPHYR_KERNEL_WRAPPER_H_
#define MP_ZEPHYR_KERNEL_WRAPPER_H_

#include <stdint.h>
#include <stdbool.h>
#include <stddef.h>

// Singly-linked list types (needed by net_buf)
#include <zephyr/sys/slist.h>

// Include our HAL headers which provide kernel abstractions
// This includes k_timeout_t from zephyr_ble_timer.h
#include "zephyr_ble_hal.h"

// Thread stack macros (from our stub)
#include <zephyr/kernel/thread_stack.h>

// Atomic operations (from our stub)
#include <zephyr/sys/atomic.h>

// Queue type (base for FIFO/LIFO)
struct k_queue {
    sys_slist_t data_q;
    struct k_spinlock lock;
};

// FIFO queue type (wrapper around k_queue, used by net_buf)
struct k_fifo {
    struct k_queue _queue;
};

// LIFO queue type (wrapper around k_queue, used by net_buf)
struct k_lifo {
    struct k_queue _queue;
};

// FIFO operations (minimal stubs for net_buf)
static inline void k_fifo_init(struct k_fifo *fifo) {
    fifo->_queue.data_q.head = NULL;
    fifo->_queue.data_q.tail = NULL;
    fifo->_queue.lock.unused = 0;
}

// LIFO operations
static inline void k_lifo_init(struct k_lifo *lifo) {
    lifo->_queue.data_q.head = NULL;
    lifo->_queue.data_q.tail = NULL;
    lifo->_queue.lock.unused = 0;
}

void k_lifo_put(struct k_lifo *lifo, void *data);
void *k_lifo_get(struct k_lifo *lifo, k_timeout_t timeout);

// Thread priority constants (used in work queue definitions)
#define K_PRIO_COOP(x) (x)
#define K_PRIO_PREEMPT(x) (x)

// Scheduler lock/unlock (no-op in cooperative scheduler)
// Implemented in zephyr_ble_kernel.c to avoid inlining issues
void k_sched_lock(void);
void k_sched_unlock(void);

// FIFO/LIFO definition macros (minimal stubs)
#define K_FIFO_DEFINE(name) struct k_fifo name
#define Z_LIFO_INITIALIZER(obj) {._queue = {.data_q = {NULL, NULL}, .lock = {0}}}

// k_fifo_put - implemented via k_queue_append
// Declared here but implemented in zephyr_ble_fifo.c
void k_fifo_put(struct k_fifo *fifo, void *data);

// k_fifo_get - implemented via k_queue_get
// Declared here but implemented in zephyr_ble_fifo.c
void *k_fifo_get(struct k_fifo *fifo, k_timeout_t timeout);

// k_fifo_peek_head - returns head without removing
static inline void *k_fifo_peek_head(struct k_fifo *fifo) {
    return fifo->_queue.data_q.head;
}

// k_queue_prepend (operates on k_queue)
// Implemented in zephyr_ble_fifo.c
void k_queue_prepend(struct k_queue *queue, void *data);

// Heap allocation stubs (map to NULL - net_buf uses pool allocation)
static inline void *k_heap_alloc(void *heap, size_t bytes, k_timeout_t timeout) {
    (void)heap;
    (void)bytes;
    (void)timeout;
    return NULL;
}

static inline void *k_heap_aligned_alloc(void *heap, size_t align, size_t bytes, k_timeout_t timeout) {
    (void)heap;
    (void)align;
    (void)bytes;
    (void)timeout;
    return NULL;
}

static inline void k_heap_free(void *heap, void *mem) {
    (void)heap;
    (void)mem;
}

// User context check (always false in cooperative scheduler)
static inline bool k_is_user_context(void) {
    return false;
}

// Timepoint type for timeout tracking (minimal stub)
typedef struct {
    uint64_t tick;
} k_timepoint_t;

// Timepoint calculation stub
static inline k_timepoint_t sys_timepoint_calc(k_timeout_t timeout) {
    k_timepoint_t tp;
    tp.tick = timeout.ticks;
    return tp;
}

// Timepoint timeout stub (convert back to timeout)
static inline k_timeout_t sys_timepoint_timeout(k_timepoint_t timepoint) {
    k_timeout_t timeout;
    timeout.ticks = (uint32_t)timepoint.tick;
    return timeout;
}

// Poll signal stub (minimal - not used in MicroPython)
struct k_poll_signal {
    int signaled;
    int result;
};

#define K_POLL_SIGNAL_INITIALIZER(obj) {0, 0}

// k_fifo_is_empty stub
// Note: In Zephyr code, k_fifo_is_empty is sometimes called with k_lifo* due to ABI compatibility
// We accept void* and cast to k_fifo* to handle both cases cleanly
static inline bool k_fifo_is_empty(void *queue) {
    struct k_fifo *fifo = (struct k_fifo *)queue;
    return sys_slist_is_empty(&fifo->_queue.data_q);
}

// Condition variable type (minimal stub - not actually used in cooperative scheduler)
struct k_condvar {
    sys_slist_t wait_q;
};

// Condition variable operations (stubs - no-op in cooperative scheduler)
// Returns 0 on success, negative errno on failure
static inline int k_condvar_wait(struct k_condvar *condvar, struct k_mutex *mutex, k_timeout_t timeout) {
    (void)condvar;
    (void)mutex;
    (void)timeout;
    // In cooperative scheduler, we can't wait - return success immediately
    return 0;
}

static inline int k_condvar_broadcast(struct k_condvar *condvar) {
    (void)condvar;
    return 0;
}

#define Z_CONDVAR_INITIALIZER(obj) {.wait_q = {NULL, NULL}}

// Mutex initializer (for static initialization)
#define Z_MUTEX_INITIALIZER(obj) {0}

// Thread name stub (no-op in cooperative scheduler)
static inline void k_thread_name_set(void *thread, const char *name) {
    (void)thread;
    (void)name;
}

// Thread abort stub (no-op - cannot abort threads in cooperative scheduler)
static inline void k_thread_abort(void *thread) {
    (void)thread;
}

// Poll signal operations
static inline void k_poll_signal_raise(struct k_poll_signal *sig, int result) {
    sig->signaled = 1;
    sig->result = result;
}

// Memory slab operations - implemented in zephyr_ble_mem_slab.c
struct k_mem_slab {
    size_t block_size;
    uint32_t num_blocks;
    void *buffer;
    void *free_list;       // Head of free block list
    uint32_t num_used;     // Number of allocated blocks
};

// Initialize a memory slab
void k_mem_slab_init(struct k_mem_slab *slab, void *buffer, size_t block_size, uint32_t num_blocks);

// Allocate/free blocks - implemented in zephyr_ble_mem_slab.c
int k_mem_slab_alloc(struct k_mem_slab *slab, void **mem, k_timeout_t timeout);
void k_mem_slab_free(struct k_mem_slab *slab, void *mem);

#define K_MEM_SLAB_DEFINE(name, slab_block_size, slab_num_blocks, slab_align) \
    static uint8_t __aligned(slab_align) \
        name##_buffer[slab_num_blocks * slab_block_size]; \
    struct k_mem_slab name = { \
        .block_size = slab_block_size, \
        .num_blocks = slab_num_blocks, \
        .buffer = name##_buffer, \
        .free_list = name##_buffer, \
        .num_used = 0 \
    }

// Static (file-scoped) memory slab definition
// Lazy initialization: free_list starts as buffer pointer, init on first alloc
#define K_MEM_SLAB_DEFINE_STATIC(name, slab_block_size, slab_num_blocks, slab_align) \
    static uint8_t __aligned(slab_align) \
        name##_buffer[slab_num_blocks * slab_block_size]; \
    static struct k_mem_slab name = { \
        .block_size = slab_block_size, \
        .num_blocks = slab_num_blocks, \
        .buffer = name##_buffer, \
        .free_list = name##_buffer, \
        .num_used = 0 \
    }

// System work queue (defined in zephyr_ble_work.c)
extern struct k_work_q k_sys_work_q;

// Note: device_is_ready() is declared in <zephyr/device.h> from real Zephyr headers
// and implemented in zephyr_ble_kernel.c

// This prevents the real kernel.h from being included later
#define ZEPHYR_INCLUDE_KERNEL_H_
// Also prevent kernel/thread.h which has conflicting k_tid_t
#define ZEPHYR_INCLUDE_KERNEL_THREAD_H_

#endif /* MP_ZEPHYR_KERNEL_WRAPPER_H_ */
