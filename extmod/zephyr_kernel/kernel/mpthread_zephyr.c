/*
 * This file is part of the MicroPython project, http://micropython.org/
 *
 * The MIT License (MIT)
 *
 * Copyright (c) 2025 MicroPython Developers
 *
 * MicroPython Threading API Implementation using Zephyr Kernel
 *
 * This file implements the mp_thread_* API required by MicroPython
 * using Zephyr kernel primitives.
 */

#include <stdio.h>
#include <string.h>

// Thread Stack Allocation Strategy
//
// Previous static pool approach: K_THREAD_STACK_ARRAY_DEFINE pre-allocated 10×4KB = 40KB
// Problems: Fixed thread limit, wasted RAM for unused slots, requires recompilation to change
//
// Current dynamic approach: Allocate stacks from MicroPython's GC heap on demand
// Benefits: No arbitrary limit (bounded by heap), zero startup cost, per-thread sizing
//
// Implementation notes:
// - Stacks allocated with gc_alloc() (8-byte aligned on ARM, sufficient for ARCH_STACK_PTR_ALIGN)
// - Stacks are NOT marked as GC roots themselves (thread struct holds reference)
// - When thread finishes, stack is freed in gc_others() cleanup or mp_thread_deinit()
// - Thread struct (mp_thread_t) is still heap-allocated via m_new_obj()

#include "py/runtime.h"
#include "py/gc.h"
#include "py/mpthread.h"
#include "py/mphal.h"
#include "py/stackctrl.h"

#include "../zephyr_kernel.h"

// Include internal Zephyr kernel headers for z_mark_thread_as_not_sleeping()
#include "../../../lib/zephyr/kernel/include/kthread.h"

// Forward declaration for z_ready_thread() (defined in kernel/sched.c)
void z_ready_thread(struct k_thread *thread);

// Thread-local storage for mp_state_thread_t pointer
// Use Zephyr's k_thread_custom_data mechanism for per-thread storage
// This works on all architectures (ARM Cortex-M, POSIX, etc.)

#if MICROPY_PY_THREAD

#define DEBUG_printf(...) // printk("mpthread: " __VA_ARGS__)

#define MP_THREAD_MIN_STACK_SIZE                (4 * 1024)
#define MP_THREAD_DEFAULT_STACK_SIZE            (MP_THREAD_MIN_STACK_SIZE)  // 4KB per thread
// Give spawned threads higher priority than main thread.
// Main thread has priority 1, spawned threads get priority 0.
// This ensures runq_best() returns the new thread instead of _current.
// Note: In Zephyr, lower number = higher priority.
#define MP_THREAD_PRIORITY                      0
// Stack alignment for ARM Cortex-M (AAPCS requires 8-byte stack alignment)
#define MP_THREAD_STACK_ALIGN                   (8)

// FPU context size validation
// With CONFIG_FPU_SHARING, each thread context switch may save/restore:
// - 32 FP registers (S0-S31): 32 * 4 = 128 bytes
// - FPSCR: 4 bytes
// Total: 132 bytes additional stack usage
#if defined(CONFIG_FPU) && defined(CONFIG_FPU_SHARING)
#define MP_THREAD_FPU_CONTEXT_SIZE              (132)
// Ensure minimum stack size accounts for FPU context overhead
#if (MP_THREAD_MIN_STACK_SIZE < (2048 + MP_THREAD_FPU_CONTEXT_SIZE))
#warning "MP_THREAD_MIN_STACK_SIZE may be too small for FPU context preservation"
#endif
#endif

typedef enum {
    MP_THREAD_STATUS_CREATED = 0,
    MP_THREAD_STATUS_READY,
    MP_THREAD_STATUS_FINISHED,
} mp_thread_status_t;

// Linked list node per active thread
typedef struct _mp_thread_t {
    k_tid_t id;                     // Zephyr thread ID
    struct k_thread z_thread;       // Zephyr thread control block
    mp_thread_status_t status;      // Thread status
    int16_t alive;                  // Whether thread is visible to kernel
    void *arg;                      // Python args (GC root pointer)
    void *stack;                    // Stack pointer (dynamically allocated)
    size_t stack_size;              // Stack size in bytes (for freeing)
    size_t stack_len;               // Stack size in words (for GC scanning)
    struct _mp_thread_t *next;      // Next in linked list
} mp_thread_t;

// Register thread list head as GC root pointer
// This ensures the main thread and linked list are scanned during GC
MP_REGISTER_ROOT_POINTER(struct _mp_thread_t *mp_thread_list_head);

// Global state
static mp_thread_mutex_t thread_mutex;
static uint8_t mp_thread_counter;

// Forward declarations
static void mp_thread_iterate_threads_cb(const struct k_thread *thread, void *user_data);
static void *mp_thread_stack_alloc(size_t size);
static void mp_thread_stack_free(void *stack, size_t size);

// Initialize threading subsystem - Phase 1 (early, before GC init)
// Sets thread-local state pointer to allow gc_init() to access MP_STATE_THREAD()
// Must be called BEFORE gc_init()
bool mp_thread_init_early(void) {
    // Set thread-local state for main thread
    // This allows gc_init() and other early code to safely access MP_STATE_THREAD()
    mp_thread_set_state(&mp_state_ctx.thread);

    DEBUG_printf("Threading early init complete (thread-local state set)\n");
    return true;
}

// Initialize threading subsystem - Phase 2 (after GC init)
// Allocates main thread structure on GC heap
// Must be called AFTER gc_init() and mp_thread_init_early()
bool mp_thread_init(void *stack) {
    // Note: mp_zephyr_kernel_init() must be called by main() BEFORE mp_thread_init_early()
    // to properly initialize the Zephyr kernel and bootstrap thread.
    // GC must be initialized BEFORE calling this function (heap allocation required)
    // mp_thread_init_early() must be called BEFORE gc_init() to set thread-local state

    // Allocate main thread on GC heap
    mp_thread_t *th = m_new_obj(mp_thread_t);

    // Initialize main thread entry in linked list
    th->id = k_current_get();
    th->status = MP_THREAD_STATUS_READY;
    th->alive = 1;
    th->arg = NULL;

    // Get main thread's stack info
    // With direct registration + early PSP switch, main thread runs on
    // z_main_stack (via PSP). The stack info is set in z_main_thread during
    // prepare_multithreading() in zephyr_cstart.c.
    extern struct k_thread z_main_thread;
    th->stack = (void *)z_main_thread.stack_info.start;
    th->stack_size = 0;  // Main thread stack is NOT dynamically allocated
    th->stack_len = z_main_thread.stack_info.size / sizeof(uintptr_t);
    th->next = NULL;

    k_thread_name_set(th->id, "mp_main");
    mp_thread_counter = 0;

    mp_thread_mutex_init(&thread_mutex);

    // Memory barrier to ensure initialization complete
    __sync_synchronize();

    MP_STATE_VM(mp_thread_list_head) = th;  // Set as head of thread list

    // Note: thread-local state already set in mp_thread_init_early()

    DEBUG_printf("Threading initialized (phase 2 complete)\n");

    return true;
}

// Clean up threading subsystem
void mp_thread_deinit(void) {
    mp_thread_mutex_lock(&thread_mutex, 1);

    // Abort all threads except current and free their stacks
    for (mp_thread_t *th = MP_STATE_VM(mp_thread_list_head); th != NULL; th = th->next) {
        if (th->id != k_current_get()) {
            if (th->status != MP_THREAD_STATUS_FINISHED) {
                th->status = MP_THREAD_STATUS_FINISHED;
                DEBUG_printf("Aborting thread %s\n", k_thread_name_get(th->id));
                k_thread_abort(th->id);
            }
            // Free dynamically allocated stack (main thread stack is not heap-allocated)
            if (th->stack != NULL && th->stack_size > 0) {
                mp_thread_stack_free(th->stack, th->stack_size);
                th->stack = NULL;
                th->stack_size = 0;
            }
        }
    }
    mp_thread_counter = 0;

    mp_thread_mutex_unlock(&thread_mutex);

    mp_zephyr_kernel_deinit();
}

// Collect garbage from other threads
void mp_thread_gc_others(void) {
    mp_thread_t *prev = NULL;

    if (MP_STATE_VM(mp_thread_list_head) == NULL) {
        return;  // Threading not initialized
    }

    mp_thread_mutex_lock(&thread_mutex, 1);

    // Ask kernel to iterate threads and mark alive ones
    DEBUG_printf("GC: Iterating threads\n");
    k_thread_foreach(mp_thread_iterate_threads_cb, NULL);

    // Clean up finished threads and collect GC roots
    mp_thread_t *th = MP_STATE_VM(mp_thread_list_head);
    while (th != NULL) {
        mp_thread_t *next = th->next;  // Capture next before any modifications

        // Remove finished, non-alive threads from list
        if ((th->status == MP_THREAD_STATUS_FINISHED) && !th->alive) {
            if (prev != NULL) {
                prev->next = next;
            } else {
                MP_STATE_VM(mp_thread_list_head) = next;
            }
            // Free the dynamically allocated stack
            if (th->stack != NULL && th->stack_size > 0) {
                mp_thread_stack_free(th->stack, th->stack_size);
                th->stack = NULL;
                th->stack_size = 0;
            }
            mp_thread_counter--;
            DEBUG_printf("GC: Collected thread %s\n", k_thread_name_get(th->id));
            // The "th" memory will eventually be reclaimed by the GC
            // Don't update prev - we removed this node
        } else {
            th->alive = 0;  // Reset for next GC cycle
            prev = th;
        }

        th = next;  // Use captured next pointer
    }

    DEBUG_printf("GC: Scanning %d threads\n", mp_thread_counter + 1);

    // Scan all remaining threads for GC roots (following ports/zephyr pattern)
    for (mp_thread_t *th = MP_STATE_VM(mp_thread_list_head); th != NULL; th = th->next) {
        DEBUG_printf("GC: Scanning thread %s\n", k_thread_name_get(th->id));

        // Mark thread structure and arg as roots
        gc_collect_root((void **)&th, 1);
        gc_collect_root(&th->arg, 1);

        // Skip current thread (its stack/registers scanned by gc_helper)
        if (th->id == k_current_get()) {
            continue;
        }

        // Skip threads not yet running or finished
        if (th->status != MP_THREAD_STATUS_READY) {
            continue;
        }

        // Scan entire stack (matches ports/zephyr approach)
        // When thread is preempted, registers are saved on stack by exception entry
        gc_collect_root(th->stack, th->stack_len);
    }

    mp_thread_mutex_unlock(&thread_mutex);
}

// Get thread-local state
mp_state_thread_t *mp_thread_get_state(void) {
    return (mp_state_thread_t *)k_thread_custom_data_get();
}

// Set thread-local state
void mp_thread_set_state(mp_state_thread_t *state) {
    k_thread_custom_data_set((void *)state);
}

// Get current thread ID
mp_uint_t mp_thread_get_id(void) {
    return (mp_uint_t)k_current_get();
}

// Mark thread as started (called by new thread)
void mp_thread_start(void) {
    mp_thread_mutex_lock(&thread_mutex, 1);
    for (mp_thread_t *th = MP_STATE_VM(mp_thread_list_head); th != NULL; th = th->next) {
        if (th->id == k_current_get()) {
            th->status = MP_THREAD_STATUS_READY;
            break;
        }
    }
    mp_thread_mutex_unlock(&thread_mutex);

    // Memory barrier to ensure status update is visible to all threads
    __sync_synchronize();
}

// Zephyr thread entry point wrapper
static void zephyr_entry(void *arg1, void *arg2, void *arg3) {
    (void)arg3;

    // arg1 contains the python thread entry point
    if (arg1) {
        void *(*entry)(void *) = arg1;
        entry(arg2);
    }
    k_thread_abort(k_current_get());
    for (;;) {
        ;  // Never reached
    }
}

// Create new thread
mp_uint_t mp_thread_create_ex(void *(*entry)(void *), void *arg, size_t *stack_size, int priority, char *name) {
    // Normalize stack_size
    if (*stack_size == 0) {
        *stack_size = MP_THREAD_DEFAULT_STACK_SIZE;
    } else if (*stack_size < MP_THREAD_MIN_STACK_SIZE) {
        *stack_size = MP_THREAD_MIN_STACK_SIZE;
    }

    // Allocate thread structure on GC heap
    mp_thread_t *th = m_new_obj(mp_thread_t);

    // Allocate stack dynamically from MicroPython heap
    // Note: We allocate outside the mutex to avoid holding the lock during allocation
    size_t allocated_stack_size = *stack_size;
    void *stack = mp_thread_stack_alloc(allocated_stack_size);
    if (stack == NULL) {
        // Allocation failed - try GC and retry once
        gc_collect();
        stack = mp_thread_stack_alloc(allocated_stack_size);
        if (stack == NULL) {
            mp_raise_msg(&mp_type_MemoryError, MP_ERROR_TEXT("can't allocate thread stack"));
        }
    }

    mp_thread_mutex_lock(&thread_mutex, 1);

    // Create Zephyr thread with K_FOREVER initially to avoid immediate reschedule
    // Then manually start the thread after full initialization
    th->id = k_thread_create(
        &th->z_thread,
        (k_thread_stack_t *)stack,
        allocated_stack_size,
        zephyr_entry,
        entry, arg, NULL,
        priority, 0, K_FOREVER  // Don't start immediately - we'll start it later
        );

    if (th->id == NULL) {
        mp_thread_mutex_unlock(&thread_mutex);
        mp_thread_stack_free(stack, allocated_stack_size);
        mp_raise_msg(&mp_type_OSError, MP_ERROR_TEXT("can't create thread"));
    }

    k_thread_name_set(th->id, (const char *)name);

    // Initialize ALL fields BEFORE adding to thread list (ports/zephyr pattern)
    // This ensures GC never sees partially initialized thread structures
    th->status = MP_THREAD_STATUS_CREATED;
    th->alive = 0;
    th->arg = arg;
    th->stack = stack;
    th->stack_size = allocated_stack_size;
    th->stack_len = allocated_stack_size / sizeof(uintptr_t);

    // Add to thread list AFTER full initialization (matches ports/zephyr)
    // New thread is blocked in mp_thread_start() waiting for this mutex,
    // so it can't access th fields until we unlock below
    th->next = MP_STATE_VM(mp_thread_list_head);
    MP_STATE_VM(mp_thread_list_head) = th;

    mp_thread_counter++;

    // Adjust stack size to leave margin (use validated allocated size)
    *stack_size = allocated_stack_size - 1024;

    DEBUG_printf("Created thread %s (id=%p)\n", name, th->id);

    mp_thread_mutex_unlock(&thread_mutex);

    // Memory barrier to ensure thread list updates are visible to all threads
    __sync_synchronize();

    // Now start the thread - this triggers the actual context switch
    k_thread_start(th->id);

    return (mp_uint_t)th->id;
}

// Create thread (standard API)
mp_uint_t mp_thread_create(void *(*entry)(void *), void *arg, size_t *stack_size) {
    // Name buffer is on stack; k_thread_name_set() copies it so this is safe.
    // Using local buffer avoids race condition with static buffer.
    char name[16];
    snprintf(name, sizeof(name), "mp_thread_%d", mp_thread_counter);
    return mp_thread_create_ex(entry, arg, stack_size, MP_THREAD_PRIORITY, name);
}

// Mark thread as finished (called by thread before exit)
void mp_thread_finish(void) {
    mp_thread_mutex_lock(&thread_mutex, 1);

    for (mp_thread_t *th = MP_STATE_VM(mp_thread_list_head); th != NULL; th = th->next) {
        if (th->id == k_current_get()) {
            th->status = MP_THREAD_STATUS_FINISHED;
            DEBUG_printf("Finishing thread %s\n", k_thread_name_get(th->id));
            break;
        }
    }

    mp_thread_mutex_unlock(&thread_mutex);
}

// Initialize mutex - use Zephyr's k_sem (binary semaphore)
// k_sem allows cross-thread lock/unlock (correct for Python Lock semantics)
// k_mutex is recursive which breaks Python Lock semantics (second acquire succeeds)
void mp_thread_mutex_init(mp_thread_mutex_t *mutex) {
    // Zero the k_sem structure before init to ensure clean state
    // This prevents corruption from stale state in global memory across soft resets
    memset(&mutex->handle, 0, sizeof(struct k_sem));
    // Binary semaphore: initial count 0, max count 1
    k_sem_init(&mutex->handle, 0, 1);
    // Give the semaphore so first acquire succeeds (lock starts unlocked)
    k_sem_give(&mutex->handle);
}

// Lock mutex
int mp_thread_mutex_lock(mp_thread_mutex_t *mutex, int wait) {
    return k_sem_take(&mutex->handle, wait ? K_FOREVER : K_NO_WAIT) == 0;
}

// Unlock mutex
void mp_thread_mutex_unlock(mp_thread_mutex_t *mutex) {
    k_sem_give(&mutex->handle);
    // NOTE: k_yield() removed - was causing deadlock during thread creation
    // When main thread creates new thread and releases thread_mutex, yielding
    // immediately causes new thread to run and block on GIL (held by main),
    // creating deadlock. Rely on timeslicing for context switches instead.
}

// GIL exit with yield for cooperative scheduling.
// The VM's GIL bounce code (py/vm.c) does: MP_THREAD_GIL_EXIT(); MP_THREAD_GIL_ENTER();
// Without k_yield() after unlock, the same thread immediately re-acquires the GIL
// before other threads can run (thread_coop.py fails). This function is called
// via the MP_THREAD_GIL_EXIT macro override in mpthreadport.h.
void mp_thread_gil_exit(void) {
    mp_thread_mutex_unlock(&MP_STATE_VM(gil_mutex));
    k_yield();
}

// Recursive mutex functions (only compiled when GIL is disabled)
// When MICROPY_PY_THREAD_GIL=1, these are not used because the GIL already
// provides serialization. When MICROPY_PY_THREAD_GIL=0, recursive mutexes
// are needed for GC protection.
#if MICROPY_PY_THREAD_RECURSIVE_MUTEX

void mp_thread_recursive_mutex_init(mp_thread_recursive_mutex_t *mutex) {
    // Zero the k_mutex structure before init to ensure clean state
    memset(&mutex->handle, 0, sizeof(struct k_mutex));
    k_mutex_init(&mutex->handle);
}

int mp_thread_recursive_mutex_lock(mp_thread_recursive_mutex_t *mutex, int wait) {
    return k_mutex_lock(&mutex->handle, wait ? K_FOREVER : K_NO_WAIT) == 0;
}

void mp_thread_recursive_mutex_unlock(mp_thread_recursive_mutex_t *mutex) {
    k_mutex_unlock(&mutex->handle);
    k_yield();  // Yield CPU to allow waiting threads to run
}

#endif // MICROPY_PY_THREAD_RECURSIVE_MUTEX

// Helper: Thread iteration callback for GC
static void mp_thread_iterate_threads_cb(const struct k_thread *z_thread, void *user_data) {
    for (mp_thread_t *th = MP_STATE_VM(mp_thread_list_head); th != NULL; th = th->next) {
        if (th->id == (struct k_thread *)z_thread) {
            th->alive = 1;
            DEBUG_printf("GC: Found thread %s\n", k_thread_name_get(th->id));
        }
    }
}

// Helper: Allocate thread stack from MicroPython GC heap
// Returns 8-byte aligned memory suitable for ARM thread stacks
static void *mp_thread_stack_alloc(size_t size) {
    // Round up to alignment boundary
    size = (size + MP_THREAD_STACK_ALIGN - 1) & ~(MP_THREAD_STACK_ALIGN - 1);

    // Allocate from GC heap with proper flags
    // GC_ALLOC_FLAG_HAS_FINALISER=0 - no finalizer needed
    // The memory is freed explicitly in mp_thread_gc_others() or mp_thread_deinit()
    void *stack = gc_alloc(size, 0);

    DEBUG_printf("Stack alloc: size=%zu ptr=%p\n", size, stack);
    return stack;
}

// Helper: Free thread stack back to MicroPython GC heap
static void mp_thread_stack_free(void *stack, size_t size) {
    (void)size;  // Size not needed for gc_free
    if (stack != NULL) {
        DEBUG_printf("Stack free: size=%zu ptr=%p\n", size, stack);
        gc_free(stack);
    }
}

#endif // MICROPY_PY_THREAD
