/*
 * Zephyr kernel.h wrapper for MicroPython
 * Redirects to our HAL implementation
 */

#ifndef ZEPHYR_KERNEL_H_
#define ZEPHYR_KERNEL_H_

// Include autoconf.h first to provide CONFIG_* defines
#include "zephyr/autoconf.h"

// Include our HAL implementation
#include "../hal/zephyr_ble_hal.h"

// Include sys/slist.h for sys_slist_t (needed by net_buf.h)
#include "zephyr/sys/slist.h"

// Include poll and thread headers
#include "zephyr/kernel/poll.h"
#include "zephyr/kernel/thread.h"

// Include Zephyr's standard types
#include <stdint.h>
#include <stddef.h>
#include <stdbool.h>
#include <string.h>
#include <stdio.h>

// Error codes
#include <errno.h>

// ESHUTDOWN may not be defined on all systems
#ifndef ESHUTDOWN
#define ESHUTDOWN 108  // Standard Linux value
#endif

// Zephyr common macros
// Note: ARRAY_SIZE is defined in sys/util.h with type checking
#define CONTAINER_OF(ptr, type, field) \
    ((type *)(((char *)(ptr)) - offsetof(type, field)))

#ifndef MIN
#define MIN(a, b) (((a) < (b)) ? (a) : (b))
#endif
#ifndef MAX
#define MAX(a, b) (((a) > (b)) ? (a) : (b))
#endif

#define BIT(n) (1UL << (n))
#define BIT_MASK(n) (BIT(n) - 1UL)

// Zephyr attributes
#ifndef __packed
#define __packed __attribute__((__packed__))
#endif
#ifndef __aligned
#define __aligned(x) __attribute__((__aligned__(x)))
#endif
#define __used __attribute__((__used__))
#define __unused __attribute__((__unused__))
#define __maybe_unused __attribute__((__unused__))
#define __deprecated __attribute__((__deprecated__))

// Zephyr build assertions - only define if not already defined by config
#ifndef BUILD_ASSERT
#define BUILD_ASSERT(cond, msg) _Static_assert(cond, msg)
#endif

// Zephyr assert macros
#ifndef __ASSERT_NO_MSG
#define __ASSERT_NO_MSG(cond) do { if (!(cond)) { __builtin_trap(); } } while (0)
#endif

#ifndef __ASSERT
// Variadic __ASSERT to support both 2 and 3+ argument versions
// __ASSERT(cond, msg) or __ASSERT(cond, fmt, ...)
#define __ASSERT(cond, ...) do { \
    if (!(cond)) { \
        (void)(__VA_ARGS__); \
        __builtin_trap(); \
    } \
} while (0)
#endif

// Note: printk is defined in zephyr/sys/printk.h

// =============================================================================
// Queue Primitives (k_queue, k_fifo, k_lifo)
// =============================================================================
// Zephyr Semantic: Threaded queues with blocking operations.
// - k_queue: Low-level queue with explicit wait_q for blocking threads
// - k_fifo: FIFO queue (wrapper around k_queue)
// - k_lifo: LIFO queue (wrapper around k_queue)
//
// MicroPython Mapping: Simplified non-blocking queues.
// - No wait queues (cooperative scheduling - no blocking)
// - No spinlocks at this layer (handled by atomic operations when needed)
// - Direct list manipulation for O(1) operations
//
// Why This Structure:
//
// In Zephyr, k_fifo/k_lifo contain a member named "_queue" of type k_queue.
// Some BLE host code accesses this directly: `fifo->_queue` or `lifo->_queue`.
// To maintain API compatibility, we provide this member even though we don't
// need the full k_queue functionality (wait queues, etc.).
//
// The _queue member is a struct containing the actual list, not just a pointer.
// This allows both `&fifo->_queue` and `&fifo->list` to be used interchangeably
// in code, as they're layout-compatible.

// k_queue: Low-level queue type
// Contains just the list - wait_q and lock omitted (not needed in cooperative scheduler)
struct k_queue {
    sys_slist_t list;  // The actual queue data structure
};

// k_lifo: LIFO (stack) queue
// In Zephyr: Contains k_queue _queue member
// In MicroPython: Provides both list and _queue for compatibility
struct k_lifo {
    union {
        sys_slist_t list;     // Direct access to list
        struct k_queue _queue;  // Zephyr-compatible access
    };
};

// k_fifo: FIFO queue
// In Zephyr: Contains k_queue _queue member
// In MicroPython: Provides both list and _queue for compatibility
struct k_fifo {
    union {
        sys_slist_t list;     // Direct access to list
        struct k_queue _queue;  // Zephyr-compatible access
    };
};

static inline void k_lifo_init(struct k_lifo *lifo) {
    lifo->list.head = NULL;
    lifo->list.tail = NULL;
}

static inline void k_fifo_init(struct k_fifo *fifo) {
    fifo->list.head = NULL;
    fifo->list.tail = NULL;
}

// FIFO static definition macro
#define K_FIFO_DEFINE(name) \
    struct k_fifo name = { .list = { .head = NULL, .tail = NULL } }

static inline void k_lifo_put(struct k_lifo *lifo, void *data) {
    sys_snode_t *node = (sys_snode_t *)data;
    node->next = lifo->list.head;
    lifo->list.head = node;
    if (lifo->list.tail == NULL) {
        lifo->list.tail = node;
    }
}

static inline void *k_lifo_get(struct k_lifo *lifo, k_timeout_t timeout) {
    (void)timeout;  // Simplified: ignore timeout
    if (lifo->list.head == NULL) {
        return NULL;
    }
    sys_snode_t *node = lifo->list.head;
    lifo->list.head = node->next;
    if (lifo->list.head == NULL) {
        lifo->list.tail = NULL;
    }
    return node;
}

// Check if LIFO is empty
static inline bool k_lifo_is_empty(struct k_lifo *lifo) {
    return lifo->list.head == NULL;
}

static inline void k_fifo_put(struct k_fifo *fifo, void *data) {
    sys_snode_t *node = (sys_snode_t *)data;
    node->next = NULL;
    if (fifo->list.tail) {
        fifo->list.tail->next = node;
    } else {
        fifo->list.head = node;
    }
    fifo->list.tail = node;
}

// LIFO initializer macro (for static initialization)
// k_lifo has a .list member of type sys_slist_t
#define Z_LIFO_INITIALIZER(obj) { .list = { .head = NULL, .tail = NULL } }

static inline void *k_fifo_get(struct k_fifo *fifo, k_timeout_t timeout) {
    return k_lifo_get((struct k_lifo *)fifo, timeout);
}

static inline bool k_fifo_is_empty(struct k_fifo *fifo) {
    return fifo->list.head == NULL;
}

// Peek at head of FIFO without removing
static inline void *k_fifo_peek_head(struct k_fifo *fifo) {
    return fifo->list.head;
}

// =============================================================================
// k_queue Operations
// =============================================================================
// Low-level queue operations that work on k_queue (and by extension k_fifo/k_lifo)
// These are used when code accesses fifo->_queue or lifo->_queue directly.

// Initialize k_queue
static inline void k_queue_init(struct k_queue *queue) {
    queue->list.head = NULL;
    queue->list.tail = NULL;
}

// Prepend item to queue (add to front - LIFO behavior)
// Used primarily for error recovery: putting failed item back at head
static inline void k_queue_prepend(struct k_queue *queue, void *data) {
    sys_snode_t *node = (sys_snode_t *)data;
    node->next = queue->list.head;
    queue->list.head = node;
    if (queue->list.tail == NULL) {
        queue->list.tail = node;
    }
}

// Append item to queue (add to back - FIFO behavior)
static inline void k_queue_append(struct k_queue *queue, void *data) {
    sys_snode_t *node = (sys_snode_t *)data;
    node->next = NULL;
    if (queue->list.tail) {
        queue->list.tail->next = node;
    } else {
        queue->list.head = node;
    }
    queue->list.tail = node;
}

// Get item from queue (remove from front)
static inline void *k_queue_get(struct k_queue *queue, k_timeout_t timeout) {
    (void)timeout;  // Non-blocking in MicroPython
    if (queue->list.head == NULL) {
        return NULL;
    }
    sys_snode_t *node = queue->list.head;
    queue->list.head = node->next;
    if (queue->list.head == NULL) {
        queue->list.tail = NULL;
    }
    return node;
}

// Check if queue is empty
static inline bool k_queue_is_empty(struct k_queue *queue) {
    return queue->list.head == NULL;
}

// Peek at head of queue without removing
static inline void *k_queue_peek_head(struct k_queue *queue) {
    return queue->list.head;
}

// ABI Compatibility Guarantee: k_fifo and k_lifo have identical memory layout
// Both structs contain only a single sys_slist_t member at offset 0.
// This allows safe casting between the types, as done in conn.c:924 where
// the comment explicitly states "In practice k_fifo == k_lifo ABI."
// These assertions verify the guarantee at compile time.
BUILD_ASSERT(sizeof(struct k_fifo) == sizeof(struct k_lifo),
             "k_fifo and k_lifo must have identical size");
BUILD_ASSERT(offsetof(struct k_fifo, list) == offsetof(struct k_lifo, list),
             "k_fifo and k_lifo must have identical layout");

// Timepoint abstraction for buffer timeout calculations
// In full Zephyr, this provides absolute time points for deadline tracking
typedef struct {
    uint32_t tick;
} k_timepoint_t;

// Calculate a timepoint from a timeout (deadline = now + timeout)
static inline k_timepoint_t sys_timepoint_calc(k_timeout_t timeout) {
    k_timepoint_t tp;
    if (timeout.ticks == 0 || timeout.ticks == (uint32_t)-1) {
        // K_NO_WAIT or K_FOREVER - no deadline
        tp.tick = timeout.ticks;
    } else {
        // Calculate absolute deadline
        tp.tick = mp_hal_ticks_ms() + timeout.ticks;
    }
    return tp;
}

// Convert timepoint back to relative timeout (timeout = deadline - now)
static inline k_timeout_t sys_timepoint_timeout(k_timepoint_t timepoint) {
    k_timeout_t timeout;
    if (timepoint.tick == 0 || timepoint.tick == (uint32_t)-1) {
        // K_NO_WAIT or K_FOREVER
        timeout.ticks = timepoint.tick;
    } else {
        uint32_t now = mp_hal_ticks_ms();
        if (timepoint.tick > now) {
            timeout.ticks = timepoint.tick - now;
        } else {
            // Deadline passed - return immediate timeout
            timeout.ticks = 0;
        }
    }
    return timeout;
}

// Check if timepoint has expired
static inline bool sys_timepoint_expired(k_timepoint_t timepoint) {
    if (timepoint.tick == (uint32_t)-1) {
        // K_FOREVER never expires
        return false;
    }
    if (timepoint.tick == 0) {
        // K_NO_WAIT always expired
        return true;
    }
    return mp_hal_ticks_ms() >= timepoint.tick;
}

// Heap allocation stubs (used by net_buf when CONFIG_NET_BUF_POOL_USAGE=1)
// In MicroPython we use the GC heap via m_malloc
struct k_heap {
    void *unused;  // Placeholder
};

// Forward declare MicroPython memory functions (defined in py/gc.h or py/misc.h)
// These will be linked when building with actual MicroPython
// Note: m_free in MicroPython requires size parameter for GC tracking
#ifndef m_malloc
void *m_malloc(size_t bytes);
void m_free(void *ptr, size_t num_bytes);
#endif

static inline void *k_heap_alloc(struct k_heap *heap, size_t size, k_timeout_t timeout) {
    (void)heap;
    (void)timeout;
    return m_malloc(size);
}

static inline void *k_heap_aligned_alloc(struct k_heap *heap, size_t align, size_t size, k_timeout_t timeout) {
    (void)heap;
    (void)align;  // MicroPython's m_malloc doesn't support alignment
    (void)timeout;
    return m_malloc(size);
}

static inline void k_heap_free(struct k_heap *heap, void *mem) {
    (void)heap;
    // Pass 0 for size - GC can determine actual size from allocation metadata
    m_free(mem, 0);
}

// Scheduler locking (no-op in MicroPython's cooperative scheduler)
static inline void k_sched_lock(void) {
    // No-op: MicroPython has cooperative scheduling
}

static inline void k_sched_unlock(void) {
    // No-op: MicroPython has cooperative scheduling
}

// Panic/fault handling (used by BT_ASSERT macros)
// In Zephyr, k_panic causes immediate halt, k_oops is recoverable
// For MicroPython, both map to trap (unrecoverable)
static inline void k_panic(void) __attribute__((noreturn));
static inline void k_panic(void) {
    __builtin_trap();
    while (1) {}  // Suppress noreturn warning
}

static inline void k_oops(void) {
    __builtin_trap();
}

// =============================================================================
// Memory Slab Allocator
// =============================================================================
// Zephyr Semantic: Fixed-size block allocator with O(1) allocation/deallocation.
// Preallocates N blocks of size S at compile time using dedicated memory pool.
// Used for frequently allocated objects (ATT requests, channels, etc.) to:
// 1. Prevent heap fragmentation in hard real-time systems
// 2. Provide deterministic O(1) allocation timing
// 3. Enable static memory accounting
//
// MicroPython Mapping: Transparent fallback to GC heap (m_malloc/m_free).
//
// Why This Mapping is Optimal for Maintainability:
//
// 1. SIMPLICITY: No hidden state, no bitmap management, no pool bookkeeping.
//    The struct only holds metadata, actual allocation is delegated to GC.
//    This reduces code complexity from ~200 lines (real slab) to ~20 lines.
//
// 2. CORRECTNESS: GC is battle-tested and handles edge cases (OOM, fragmentation)
//    better than a custom allocator. Fewer bugs = better maintainability.
//
// 3. MEMORY EFFICIENCY: Preallocating pools wastes RAM. GC only allocates what's
//    actually used. On embedded systems, RAM is often more constrained than CPU.
//
// 4. DEBUGGABILITY: GC allocations are traceable. Memory leaks show up in
//    gc.mem_info(). Custom pools hide allocations from introspection.
//
// 5. PERFORMANCE: BLE operates at millisecond timescales (connection intervals
//    typically 7.5ms-4000ms). GC allocation overhead (<10μs) is negligible
//    compared to radio transmission time (376μs for 27-byte packet at 1Mbps).
//
// Tradeoffs Accepted:
//
// - Lost O(1) guarantee: GC allocation is typically O(1) but can trigger
//   collection (O(heap_size)). Acceptable because:
//   a) Cooperative scheduling allows collection during idle time
//   b) BLE operations have millisecond-scale tolerances
//   c) Can tune GC thresholds to reduce collection frequency
//
// - Lost determinism: Allocation time varies with heap fragmentation.
//   Acceptable because MicroPython isn't a hard real-time system.
//
// Future Optimization Path:
//
// If profiling shows allocation as bottleneck (unlikely), can implement real
// slab by:
// 1. Adding void *pool pointer to struct
// 2. Implementing bitmap-based allocation in k_mem_slab_alloc
// 3. No API changes needed - callers unaffected
//
// This progressive enhancement approach is superior to premature optimization.

struct k_mem_slab {
    size_t block_size;  // Size of each block (bytes) - used for m_malloc
    size_t num_blocks;  // Number of blocks (metadata only - informational)
    size_t align;       // Alignment requirement (metadata only - GC handles this)
};

// Define a static memory slab
// In Zephyr: Allocates actual memory pool at compile time
// In MicroPython: Creates metadata struct only, no pool allocated
// Note: Parameter name is 'align_val' not 'align' to avoid conflict with struct member name
#define K_MEM_SLAB_DEFINE_STATIC(name, bsize, nblocks, align_val) \
    static struct k_mem_slab name = { \
        .block_size = (bsize), \
        .num_blocks = (nblocks), \
        .align = (align_val) \
    }

// Allocate block from slab
// Returns: 0 on success, -ENOMEM on failure
// Timeout parameter ignored - GC allocation is non-blocking
static inline int k_mem_slab_alloc(struct k_mem_slab *slab, void **mem, k_timeout_t timeout) {
    (void)timeout;  // GC allocation doesn't support timeout
    *mem = m_malloc(slab->block_size);
    if (*mem == NULL) {
        return -ENOMEM;
    }
    return 0;
}

// Free block back to slab
// Pass block_size to m_free for proper GC tracking
static inline void k_mem_slab_free(struct k_mem_slab *slab, void *mem) {
    m_free(mem, slab->block_size);
}

// Get number of free blocks in slab
// Returns: 1 (conceptually "memory available")
// Rationale: GC heap conceptually always has space - will trigger collection
// if needed. Returning 0 would cause false allocation failures in caller logic.
// Returning actual GC free space would be expensive (requires heap traversal).
static inline unsigned int k_mem_slab_num_free_get(struct k_mem_slab *slab) {
    (void)slab;
    return 1;
}

// Get number of used blocks in slab
// Returns: 0 (unknown - GC doesn't track per-"slab" usage)
// Rationale: Tracking usage would require maintaining parallel state, defeating
// the simplicity goal. This function is only used for debugging/stats in Zephyr.
static inline unsigned int k_mem_slab_num_used_get(struct k_mem_slab *slab) {
    (void)slab;
    return 0;
}

// Get maximum number of blocks ever used (high water mark)
// Returns: 0 (unknown - not tracked)
// Rationale: Same as num_used_get - would require parallel state
static inline unsigned int k_mem_slab_max_used_get(struct k_mem_slab *slab) {
    (void)slab;
    return 0;
}

// System work queue stub (not used in MicroPython)
// Forward declare to avoid circular dependency
struct k_work_q;
extern struct k_work_q k_sys_work_q;

#endif /* ZEPHYR_KERNEL_H_ */
