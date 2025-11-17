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

// Thread allocation strategy selection
// Testing revealed: static pool WORSENS results (25/42 pass vs 26/40 with heap)
// Hypothesis that "GC corrupts mp_thread_t" is FALSE
// Reverting to m_new_obj heap allocation which achieves better compatibility (65%)
// Future investigation: failures are NOT due to thread pointer corruption

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
#define MP_THREAD_DEFAULT_STACK_SIZE            (MP_THREAD_MIN_STACK_SIZE + 1024)
#define MP_THREAD_PRIORITY                      K_PRIO_PREEMPT(0)  // Higher priority than main (1)
#define MP_THREAD_MAXIMUM_USER_THREADS          (5)  // Reduced from 8 to save RAM (allows mutate_bytearray.py with 4 threads)

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

typedef struct _mp_thread_stack_slot_t {
    bool used;
} mp_thread_stack_slot_t;

// Forward declaration for circular reference
typedef struct _mp_thread_protected_t mp_thread_protected_t;

// Linked list node per active thread
typedef struct _mp_thread_t {
    k_tid_t id;                     // Zephyr thread ID
    struct k_thread z_thread;       // Zephyr thread control block
    mp_thread_status_t status;      // Thread status
    int16_t alive;                  // Whether thread is visible to kernel
    int16_t slot;                   // Stack slot index
    void *arg;                      // Python args (GC root pointer)
    void *stack;                    // Stack pointer
    size_t stack_len;               // Stack size in words
    mp_thread_protected_t *protected; // Back-reference to protected structure (GC root)
    struct _mp_thread_t *next;      // Next in linked list
} mp_thread_t;

// Memory corruption detection: canary protection
#define CANARY_BEFORE 0xDEADBEEF
#define CANARY_AFTER  0xBEEFDEAD

typedef struct _mp_thread_protected_t {
    uint32_t canary_before;
    mp_thread_t thread;
    uint32_t canary_after;
} mp_thread_protected_t;

// Dump memory around address for corruption analysis
static void dump_memory_region(const char *label, uint32_t *addr, int words_before, int words_after) {
    mp_printf(&mp_plat_print, "\r\n%s:\r\n", label);
    uint32_t *start = addr - words_before;
    uint32_t *end = addr + words_after;

    for (uint32_t *p = start; p < end; p += 4) {
        mp_printf(&mp_plat_print, "  %p: %08x %08x %08x %08x",
                  p, (unsigned int)p[0], (unsigned int)p[1],
                  (unsigned int)p[2], (unsigned int)p[3]);

        // Mark the corrupted canary location
        if (p <= addr && addr < p + 4) {
            mp_printf(&mp_plat_print, " <- CANARY");
        }
        mp_printf(&mp_plat_print, "\r\n");
    }
}

// Check thread structure for buffer overflow corruption
static void check_thread_canaries(mp_thread_t *th, const char *location) {
    // Calculate offset to protected structure
    mp_thread_protected_t *protected =
        (mp_thread_protected_t *)((char *)th - offsetof(mp_thread_protected_t, thread));

    if (protected->canary_before != CANARY_BEFORE) {
        mp_printf(&mp_plat_print, "\r\n*** CANARY CORRUPTED (BEFORE) ***\r\n");
        mp_printf(&mp_plat_print, "  Thread: %p\r\n", th);
        mp_printf(&mp_plat_print, "  Protected struct: %p\r\n", protected);
        mp_printf(&mp_plat_print, "  Location: %s\r\n", location);
        mp_printf(&mp_plat_print, "  Expected: 0x%08x\r\n", (unsigned int)CANARY_BEFORE);
        mp_printf(&mp_plat_print, "  Found: 0x%08x\r\n", (unsigned int)protected->canary_before);

        // Dump memory around corruption
        uint32_t *canary_addr = &protected->canary_before;
        dump_memory_region("Memory 32 bytes before canary", canary_addr, 8, 0);
        dump_memory_region("Canary + 32 bytes after", canary_addr, 0, 8);
        dump_memory_region("Thread structure", (uint32_t *)th, 0, 16);

        while(1) {
            __asm volatile("nop");  // Hang for GDB attachment
        }
    }

    if (protected->canary_after != CANARY_AFTER) {
        mp_printf(&mp_plat_print, "\r\n*** CANARY CORRUPTED (AFTER) ***\r\n");
        mp_printf(&mp_plat_print, "  Thread: %p\r\n", th);
        mp_printf(&mp_plat_print, "  Protected struct: %p\r\n", protected);
        mp_printf(&mp_plat_print, "  Location: %s\r\n", location);
        mp_printf(&mp_plat_print, "  Expected: 0x%08x\r\n", (unsigned int)CANARY_AFTER);
        mp_printf(&mp_plat_print, "  Found: 0x%08x\r\n", (unsigned int)protected->canary_after);

        // Dump memory around corruption
        uint32_t *canary_addr = &protected->canary_after;
        dump_memory_region("Thread structure", (uint32_t *)th, 0, 16);
        dump_memory_region("Memory 32 bytes before canary", canary_addr, 8, 0);
        dump_memory_region("Canary + 32 bytes after", canary_addr, 0, 8);

        while(1) {
            __asm volatile("nop");  // Hang for GDB attachment
        }
    }
}

// Register thread list head as GC root pointer
// This ensures the main thread and linked list are scanned during GC
MP_REGISTER_ROOT_POINTER(struct _mp_thread_t *mp_thread_list_head);

// Global state
static mp_thread_mutex_t thread_mutex;
static uint8_t mp_thread_counter;
static mp_thread_stack_slot_t stack_slot[MP_THREAD_MAXIMUM_USER_THREADS];

// Pre-allocated stack pool
K_THREAD_STACK_ARRAY_DEFINE(mp_thread_stack_array, MP_THREAD_MAXIMUM_USER_THREADS, MP_THREAD_DEFAULT_STACK_SIZE);

// Forward declarations
static void mp_thread_iterate_threads_cb(const struct k_thread *thread, void *user_data);
static int32_t mp_thread_find_stack_slot(void);

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

    // Allocate main thread with canary protection (same as all other threads)
    mp_thread_protected_t *protected = m_new_obj(mp_thread_protected_t);
    protected->canary_before = CANARY_BEFORE;
    protected->canary_after = CANARY_AFTER;
    mp_thread_t *th = &protected->thread;

    // Initialize main thread entry in linked list
    th->id = k_current_get();
    th->status = MP_THREAD_STATUS_READY;
    th->alive = 1;
    th->arg = NULL;
    th->protected = protected;  // Back-reference for GC

    // Get main thread's stack info from Zephyr (matches ports/zephyr pattern)
    // The main thread was created by Zephyr before mp_thread_init(), so we
    // query its existing stack_info rather than setting it ourselves
    th->stack = (void *)th->id->stack_info.start;
    th->stack_len = th->id->stack_info.size / sizeof(uintptr_t);
    th->next = NULL;

    k_thread_name_set(th->id, "mp_main");
    mp_thread_counter = 0;

    mp_thread_mutex_init(&thread_mutex);

    // Memory barrier to ensure initialization complete
    __sync_synchronize();

    MP_STATE_VM(mp_thread_list_head) = th;  // Set as head of thread list

    // Validate canaries after main thread initialization
    check_thread_canaries(th, "mp_thread_init main thread");

    // Note: thread-local state already set in mp_thread_init_early()

    DEBUG_printf("Threading initialized (phase 2 complete)\n");

    return true;
}

// Clean up threading subsystem
void mp_thread_deinit(void) {
    mp_thread_mutex_lock(&thread_mutex, 1);

    // Abort all threads except current
    for (mp_thread_t *th = MP_STATE_VM(mp_thread_list_head); th != NULL; th = th->next) {
        if ((th->id != k_current_get()) && (th->status != MP_THREAD_STATUS_FINISHED)) {
            th->status = MP_THREAD_STATUS_FINISHED;
            DEBUG_printf("Aborting thread %s\n", k_thread_name_get(th->id));
            k_thread_abort(th->id);
        }
    }

    mp_thread_mutex_unlock(&thread_mutex);

    mp_zephyr_kernel_deinit();
}

// Collect garbage from other threads
void mp_thread_gc_others(void) {
    mp_thread_t *prev = NULL;

    if (MP_STATE_VM(mp_thread_list_head) == NULL) {
        return;  // Threading not initialized
    }

    // VALIDATION: Check thread list integrity before GC
    int thread_count = 0;
    for (mp_thread_t *th = MP_STATE_VM(mp_thread_list_head); th != NULL; th = th->next) {
        thread_count++;
        if (thread_count > MP_THREAD_MAXIMUM_USER_THREADS + 1) {
            mp_printf(&mp_plat_print,
                      "*** GC VALIDATION: Thread list corrupted (too many threads=%d) ***\n",
                      thread_count);
            while(1) { __asm volatile("nop"); }  // Hang for GDB
        }

        // Validation removed - was checking stale th->stack_len from initialization
        // before we recalculate based on saved psp
    }

    mp_thread_mutex_lock(&thread_mutex, 1);

    // Ask kernel to iterate threads and mark alive ones
    DEBUG_printf("GC: Iterating threads\n");
    k_thread_foreach(mp_thread_iterate_threads_cb, NULL);

    // Clean up finished threads and collect GC roots
    mp_thread_t *th = MP_STATE_VM(mp_thread_list_head);
    while (th != NULL) {
        mp_thread_t *next = th->next;  // Capture next before any modifications

        // Validate canaries during thread list iteration
        check_thread_canaries(th, "mp_thread_gc_others cleanup");

        // Remove finished, non-alive threads from list
        if ((th->status == MP_THREAD_STATUS_FINISHED) && !th->alive) {
            if (prev != NULL) {
                prev->next = next;
            } else {
                MP_STATE_VM(mp_thread_list_head) = next;
            }
            if (th->slot >= 0 && th->slot < MP_THREAD_MAXIMUM_USER_THREADS) {
                stack_slot[th->slot].used = false;
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

    // Scan all remaining threads for GC roots
    for (mp_thread_t *th = MP_STATE_VM(mp_thread_list_head); th != NULL; th = th->next) {
        DEBUG_printf("GC: Scanning thread %s\n", k_thread_name_get(th->id));

        // Validate canaries during GC scan
        check_thread_canaries(th, "mp_thread_gc_others scan");

        // CHECK FOR 0x1d CORRUPTION: Examine thread_state and nearby pointers
        uint8_t thread_state = th->z_thread.base.thread_state;
        if (thread_state == 0x1d) {
            // Found 0x1d state - check if any pointer fields are corrupted
            mp_printf(&mp_plat_print, "\n*** FOUND 0x1d THREAD STATE in thread %s ***\n", k_thread_name_get(th->id));
            mp_printf(&mp_plat_print, "  Thread @ %p\n", th);
            mp_printf(&mp_plat_print, "  stack:     %p\n", th->stack);
            mp_printf(&mp_plat_print, "  next:      %p\n", th->next);
            mp_printf(&mp_plat_print, "  arg:       %p\n", th->arg);

            // Check if any pointer has 0x1d in high byte
            if (((uintptr_t)th->stack & 0xFF000000) == 0x1d000000) {
                mp_printf(&mp_plat_print, "  >>> CORRUPTION: stack has 0x1d high byte!\n");
            }
            if (((uintptr_t)th->next & 0xFF000000) == 0x1d000000) {
                mp_printf(&mp_plat_print, "  >>> CORRUPTION: next has 0x1d high byte!\n");
            }
            if (((uintptr_t)th->arg & 0xFF000000) == 0x1d000000) {
                mp_printf(&mp_plat_print, "  >>> CORRUPTION: arg has 0x1d high byte!\n");
            }
        }

        gc_collect_root((void **)&th, 1);
        gc_collect_root(&th->arg, 1);
        gc_collect_root((void **)&th->protected, 1);  // Mark full protected structure

        if (th->id == k_current_get()) {
            continue;  // Don't scan current thread's stack (done separately)
        }

        if (th->status != MP_THREAD_STATUS_READY) {
            continue;  // Thread not running
        }

        // Scan entire stack allocation (matches ports/zephyr proven pattern)
        // Conservative GC safely handles uninitialized stack regions
        if (th->id == NULL) {
            continue;  // Defensive: should never happen if status==READY
        }

        th->stack = (void *)th->id->stack_info.start;
        th->stack_len = th->id->stack_info.size / sizeof(uintptr_t);
        gc_collect_root(th->stack, th->stack_len);

        // Scan saved callee registers (r4-r11)
        void **saved_regs = (void **)&th->id->callee_saved;
        gc_collect_root(saved_regs, 8);  // v1-v8 = r4-r11 (8 registers)
    }

    // VALIDATION: Verify we scanned all expected threads
    DEBUG_printf("GC validation: Scanned %d threads total\n", thread_count);
    if (thread_count == 0) {
        mp_printf(&mp_plat_print, "*** GC VALIDATION: NO THREADS SCANNED! ***\n");
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
    // TODO: we need to support CONFIG_DYNAMIC_THREAD in order to dynamically allocate the stack of a thread
    // For now we use statically allocated stacks, so stack_size parameter is ignored during creation
    // but we still update it to reflect the actual stack size allocated
    // if (*stack_size == 0) {
    //     *stack_size = MP_THREAD_DEFAULT_STACK_SIZE;
    // } else if (*stack_size < MP_THREAD_MIN_STACK_SIZE) {
    //     *stack_size = MP_THREAD_MIN_STACK_SIZE;
    // }

    // NOTE: gc_collect() removed from here - it was causing deadlock with spawned threads
    // trying to lock thread_mutex while main thread was in gc_collect(). The normal GC
    // cycles will clean up finished threads anyway.

    // Heap allocation with canary protection for memory corruption detection
    // Allocate mp_thread_protected_t (contains canaries before/after mp_thread_t)
    // Static pool testing showed: 25/42 pass (worse than 26/40 with heap)
    mp_thread_protected_t *protected = m_new_obj(mp_thread_protected_t);
    protected->canary_before = CANARY_BEFORE;
    protected->canary_after = CANARY_AFTER;
    mp_thread_t *th = &protected->thread;

    // Initialize ALL fields to safe values before adding to list
    // This prevents GC from scanning uninitialized memory as pointers
    th->id = NULL;
    th->status = 0;  // Will be set to MP_THREAD_STATUS_CREATED later
    th->alive = 0;
    th->slot = -1;  // Will be set to actual slot later
    th->arg = NULL;  // CRITICAL: GC scans this, must not be garbage
    th->stack = NULL;
    th->stack_len = 0;  // GC will skip stack scan until this is set
    th->protected = protected;  // Back-reference for GC (CRITICAL)
    th->next = NULL;

    mp_thread_mutex_lock(&thread_mutex, 1);

    // Add to list IMMEDIATELY after allocation and before full initialization
    // This protects the thread from being collected as garbage if gc_collect()
    // is triggered during the rest of initialization (e.g., in k_thread_create)
    th->next = MP_STATE_VM(mp_thread_list_head);
    MP_STATE_VM(mp_thread_list_head) = th;

    int32_t _slot = mp_thread_find_stack_slot();
    if (_slot < 0) {
        // Remove from list before failing
        MP_STATE_VM(mp_thread_list_head) = th->next;
        mp_thread_mutex_unlock(&thread_mutex);
        mp_raise_msg(&mp_type_OSError, MP_ERROR_TEXT("maximum number of threads reached"));
    }

    // Store allocated stack size for validation
    size_t allocated_stack_size = K_THREAD_STACK_SIZEOF(mp_thread_stack_array[_slot]);

    // CRITICAL: Initialize fields BEFORE starting thread to prevent GC race
    // If thread starts immediately (K_NO_WAIT) and triggers GC, mp_thread_gc_others()
    // will scan th->arg at line 350. Must be valid before thread runs.
    th->status = MP_THREAD_STATUS_CREATED;
    th->alive = 0;
    th->slot = _slot;
    th->arg = arg;  // MUST be set before k_thread_create() for GC safety

    // Create Zephyr thread with K_NO_WAIT to start immediately
    // This matches the ports/zephyr pattern
    th->id = k_thread_create(
        &th->z_thread,
        mp_thread_stack_array[_slot],
        allocated_stack_size,
        zephyr_entry,
        entry, arg, NULL,
        priority, 0, K_NO_WAIT
        );

    if (th->id == NULL) {
        // Remove from list before failing
        MP_STATE_VM(mp_thread_list_head) = th->next;
        mp_thread_mutex_unlock(&thread_mutex);
        // Memory will be reclaimed by GC
        mp_raise_msg(&mp_type_OSError, MP_ERROR_TEXT("can't create thread"));
    }

    k_thread_name_set(th->id, (const char *)name);

    // Validate that Zephyr's stack_info matches our allocation
    // This detects if stack_info.size is unreliable (as suspected in 0x1d corruption investigation)
    if (th->z_thread.stack_info.size != allocated_stack_size) {
        mp_printf(&mp_plat_print,
                  "WARNING: stack_info.size mismatch for thread %s\r\n"
                  "  Allocated: %zu bytes\r\n"
                  "  Reported:  %zu bytes\r\n"
                  "  Using allocated size for safety\r\n",
                  name, allocated_stack_size, th->z_thread.stack_info.size);
    }

    // Complete stack initialization (status/arg/slot already set above for GC safety)
    th->stack = (void *)th->z_thread.stack_info.start;
    // Use allocated size (validated against stack_info above)
    th->stack_len = allocated_stack_size / sizeof(uintptr_t);
    // Note: th->next already set when added to list earlier

    stack_slot[_slot].used = true;
    mp_thread_counter++;

    // Adjust stack size to leave margin (use validated allocated size)
    *stack_size = allocated_stack_size - 1024;

    DEBUG_printf("Created thread %s (id=%p)\n", name, th->id);

    // Validate canaries after thread initialization
    check_thread_canaries(th, "mp_thread_create_ex after init");

    mp_thread_mutex_unlock(&thread_mutex);

    // Memory barrier to ensure thread list updates are visible to all threads
    __sync_synchronize();

    return (mp_uint_t)th->id;
}

// Create thread (standard API)
mp_uint_t mp_thread_create(void *(*entry)(void *), void *arg, size_t *stack_size) {
    // Allocate name on GC heap to avoid static buffer race condition
    // Previous: static char name[16] shared across ALL threads (corrupted on concurrent create)
    // Each thread needs its own name buffer for thread-safe operation
    char *name = m_new(char, 16);
    snprintf(name, 16, "mp_thread_%d", mp_thread_counter);
    return mp_thread_create_ex(entry, arg, stack_size, MP_THREAD_PRIORITY, name);
}

// Mark thread as finished (called by thread before exit)
void mp_thread_finish(void) {
    mp_thread_mutex_lock(&thread_mutex, 1);

    for (mp_thread_t *th = MP_STATE_VM(mp_thread_list_head); th != NULL; th = th->next) {
        if (th->id == k_current_get()) {
            // Validate canaries before marking thread finished
            check_thread_canaries(th, "mp_thread_finish");
            th->status = MP_THREAD_STATUS_FINISHED;
            DEBUG_printf("Finishing thread %s\n", k_thread_name_get(th->id));
            break;
        }
    }

    mp_thread_mutex_unlock(&thread_mutex);
}

// Initialize mutex - use Zephyr's k_sem (binary semaphore, non-recursive)
// Non-recursive behavior is required for Python threading semantics
void mp_thread_mutex_init(mp_thread_mutex_t *mutex) {
    // Zero the k_sem structure before init to ensure clean state
    // This prevents corruption from stale state in global memory across soft resets
    memset(&mutex->handle, 0, sizeof(struct k_sem));
    k_sem_init(&mutex->handle, 0, 1);
    k_sem_give(&mutex->handle);
}

// Lock mutex
int mp_thread_mutex_lock(mp_thread_mutex_t *mutex, int wait) {
    // DEBUG logging disabled - was causing reentrancy issues
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

// Helper: Find available stack slot
static int32_t mp_thread_find_stack_slot(void) {
    for (int i = 0; i < MP_THREAD_MAXIMUM_USER_THREADS; i++) {
        if (!stack_slot[i].used) {
            DEBUG_printf("Allocating stack slot %d\n", i);
            return i;
        }
    }
    return -1;
}


#endif // MICROPY_PY_THREAD
