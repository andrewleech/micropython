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

#include "py/runtime.h"
#include "py/gc.h"
#include "py/mpthread.h"
#include "py/mphal.h"
#include "py/stackctrl.h"

#include "../zephyr_kernel.h"

// Thread-local storage for mp_state_thread_t pointer
// Use Zephyr's k_thread_custom_data mechanism for per-thread storage
// This works on all architectures (ARM Cortex-M, POSIX, etc.)

#if MICROPY_PY_THREAD

#define DEBUG_printf(...) // printk("mpthread: " __VA_ARGS__)

#define MP_THREAD_MIN_STACK_SIZE                (4 * 1024)
#define MP_THREAD_DEFAULT_STACK_SIZE            (MP_THREAD_MIN_STACK_SIZE + 1024)
#define MP_THREAD_PRIORITY                      K_PRIO_PREEMPT(0)  // Higher priority than main (1)
#define MP_THREAD_MAXIMUM_USER_THREADS          (8)

typedef enum {
    MP_THREAD_STATUS_CREATED = 0,
    MP_THREAD_STATUS_READY,
    MP_THREAD_STATUS_FINISHED,
} mp_thread_status_t;

typedef struct _mp_thread_stack_slot_t {
    bool used;
} mp_thread_stack_slot_t;

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
    mp_state_thread_t *thread_state;// Python thread state (GC root)
    struct _mp_thread_t *next;      // Next in linked list
} mp_thread_t;

// Global state
static mp_thread_mutex_t thread_mutex;
static mp_thread_t thread_entry0;          // Main thread entry
static mp_thread_t *thread = NULL;         // Thread list head (GC root)
static uint8_t mp_thread_counter;
static mp_thread_stack_slot_t stack_slot[MP_THREAD_MAXIMUM_USER_THREADS];

// Pre-allocated stack pool
K_THREAD_STACK_ARRAY_DEFINE(mp_thread_stack_array, MP_THREAD_MAXIMUM_USER_THREADS, MP_THREAD_DEFAULT_STACK_SIZE);

// Forward declarations
static void mp_thread_iterate_threads_cb(const struct k_thread *thread, void *user_data);
static int32_t mp_thread_find_stack_slot(void);

// Initialize threading subsystem
bool mp_thread_init(void *stack) {
    // Note: mp_zephyr_kernel_init() must be called by main() BEFORE mp_thread_init()
    // to properly initialize the Zephyr kernel and bootstrap thread.

    // Create first entry in linked list (main thread running on bootstrap)
    thread_entry0.id = k_current_get();
    thread_entry0.status = MP_THREAD_STATUS_READY;
    thread_entry0.alive = 1;
    thread_entry0.arg = NULL;
    thread_entry0.stack = stack;
    thread_entry0.stack_len = 0;  // Bootstrap thread uses C runtime stack, size unknown
    thread_entry0.thread_state = &mp_state_ctx.thread;  // Main thread uses global state
    thread_entry0.next = NULL;

    k_thread_name_set(thread_entry0.id, "mp_main");
    mp_thread_counter = 0;

    mp_thread_mutex_init(&thread_mutex);

    // Memory barrier to ensure initialization complete
    __sync_synchronize();

    thread = &thread_entry0;

    // Set thread-local state for main thread
    mp_thread_set_state(&mp_state_ctx.thread);

    DEBUG_printf("Threading initialized\n");

    return true;
}

// Clean up threading subsystem
void mp_thread_deinit(void) {
    mp_thread_mutex_lock(&thread_mutex, 1);

    // Abort all threads except current
    for (mp_thread_t *th = thread; th != NULL; th = th->next) {
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

    if (thread == NULL) {
        return;  // Threading not initialized
    }

    mp_thread_mutex_lock(&thread_mutex, 1);

    // Ask kernel to iterate threads and mark alive ones
    DEBUG_printf("GC: Iterating threads\n");
    k_thread_foreach(mp_thread_iterate_threads_cb, NULL);

    // Clean up finished threads and collect GC roots
    mp_thread_t *th = thread;
    while (th != NULL) {
        mp_thread_t *next = th->next;  // Capture next before any modifications

        // Remove finished, non-alive threads from list
        if ((th->status == MP_THREAD_STATUS_FINISHED) && !th->alive) {
            if (prev != NULL) {
                prev->next = next;
            } else {
                thread = next;
            }
            if (th->slot >= 0 && th->slot < MP_THREAD_MAXIMUM_USER_THREADS) {
                stack_slot[th->slot].used = false;
            }
            mp_thread_counter--;
            DEBUG_printf("GC: Collected thread %s\n", k_thread_name_get(th->id));
            // Don't update prev - we removed this node
        } else {
            th->alive = 0;  // Reset for next GC cycle
            prev = th;
        }

        th = next;  // Use captured next pointer
    }

    DEBUG_printf("GC: Scanning %d threads\n", mp_thread_counter + 1);

    // Scan all remaining threads for GC roots
    for (mp_thread_t *th = thread; th != NULL; th = th->next) {
        DEBUG_printf("GC: Scanning thread %s\n", k_thread_name_get(th->id));

        gc_collect_root((void **)&th, 1);
        gc_collect_root(&th->arg, 1);
        gc_collect_root((void **)&th->thread_state, 1);  // Scan thread state

        if (th->id == k_current_get()) {
            continue;  // Don't scan current thread's stack (done separately)
        }

        if (th->status != MP_THREAD_STATUS_READY) {
            continue;  // Thread not running
        }

        // Scan the thread's stack
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
    // Update status without locking - the thread list is only modified by
    // the main thread, and we're only updating our own status field.
    // The status field is used for informational purposes only.
    for (mp_thread_t *th = thread; th != NULL; th = th->next) {
        if (th->id == k_current_get()) {
            th->status = MP_THREAD_STATUS_READY;
            break;
        }
    }
}

// Zephyr thread entry point wrapper
static void zephyr_entry(void *arg1, void *arg2, void *arg3) {
    (void)arg3;

    // Get the mp_thread_t structure for this thread (passed via arg3 actually stored in custom_data)
    // Find our thread in the list
    mp_thread_t *self = NULL;
    k_tid_t current_tid = k_current_get();

    mp_thread_mutex_lock(&thread_mutex, 1);
    for (mp_thread_t *th = thread; th != NULL; th = th->next) {
        if (th->id == current_tid) {
            self = th;
            break;
        }
    }
    mp_thread_mutex_unlock(&thread_mutex);

    if (self == NULL || self->thread_state == NULL) {
        // Fatal error - thread state not set up
        k_thread_abort(current_tid);
        for (;;) { ; }
    }

    // Set up thread-local storage using pre-allocated state
    mp_thread_set_state(self->thread_state);

    // Get stack info from current thread
    struct k_thread *current = k_current_get();
    void *stack_top = (void *)((uintptr_t)current->stack_info.start + current->stack_info.size);
    size_t stack_limit = current->stack_info.size - 1024;  // Leave margin for Zephyr

    // Set up stack bounds for Python stack checking
    mp_stack_set_top(stack_top);
    mp_stack_set_limit(stack_limit);

    // Mark thread as started (updates status to READY)
    mp_thread_start();

    // Now safe to execute Python code
    if (arg1) {
        void *(*entry)(void *) = arg1;
        entry(arg2);
    }

    // Clean up when thread finishes
    mp_thread_finish();
    k_thread_abort(k_current_get());
    for (;;) {
        ;  // Never reached
    }
}

// Create new thread
mp_uint_t mp_thread_create_ex(void *(*entry)(void *), void *arg, size_t *stack_size, int priority, char *name) {
    // Default stack size
    if (*stack_size == 0) {
        *stack_size = MP_THREAD_DEFAULT_STACK_SIZE;
    } else if (*stack_size < MP_THREAD_MIN_STACK_SIZE) {
        *stack_size = MP_THREAD_MIN_STACK_SIZE;
    }

    // Try to garbage collect old threads
    gc_collect();

    // Allocate thread node and thread state (must be outside mutex lock for GC)
    mp_thread_t *th = m_new_obj(mp_thread_t);
    mp_state_thread_t *ts = m_new_obj(mp_state_thread_t);
    memset(ts, 0, sizeof(mp_state_thread_t));

    mp_thread_mutex_lock(&thread_mutex, 1);

    // Find available stack slot
    int32_t _slot = mp_thread_find_stack_slot();
    if (_slot < 0) {
        mp_thread_mutex_unlock(&thread_mutex);
        m_del_obj(mp_state_thread_t, ts);  // Clean up allocated memory
        m_del_obj(mp_thread_t, th);
        mp_raise_msg(&mp_type_OSError, MP_ERROR_TEXT("maximum number of threads reached"));
    }

    // Create Zephyr thread with K_NO_WAIT so it's ready to run immediately
    // We'll manually control when it starts via mp_zephyr_thread_start()
    th->id = k_thread_create(
        &th->z_thread,
        mp_thread_stack_array[_slot],
        K_THREAD_STACK_SIZEOF(mp_thread_stack_array[_slot]),
        zephyr_entry,
        entry, arg, NULL,
        priority, 0, K_NO_WAIT
        );

    if (th->id == NULL) {
        mp_thread_mutex_unlock(&thread_mutex);
        m_del_obj(mp_state_thread_t, ts);  // Clean up allocated memory
        m_del_obj(mp_thread_t, th);
        mp_raise_msg(&mp_type_OSError, MP_ERROR_TEXT("can't create thread"));
    }

    k_thread_name_set(th->id, (const char *)name);

    // Add to linked list
    th->status = MP_THREAD_STATUS_CREATED;
    th->alive = 0;
    th->slot = _slot;
    th->arg = arg;
    th->stack = (void *)th->z_thread.stack_info.start;
    th->stack_len = th->z_thread.stack_info.size / sizeof(uintptr_t);
    th->thread_state = ts;  // Store pre-allocated thread state
    th->next = thread;
    thread = th;

    stack_slot[_slot].used = true;
    mp_thread_counter++;

    // Adjust stack size to leave margin
    *stack_size = th->z_thread.stack_info.size - 1024;

    mp_thread_mutex_unlock(&thread_mutex);

    DEBUG_printf("Created thread %s (id=%p)\n", name, th->id);

    // Manually start the thread using our custom function that calls z_ready_thread
    // (k_thread_start uses k_wakeup which only works for sleeping threads)
    // IMPORTANT: This must be called AFTER unlocking the mutex to allow context switch
    extern void mp_zephyr_thread_start(struct k_thread *thread);
    mp_zephyr_thread_start(th->id);

    // Note: The new thread will run when PendSV fires (triggered by mp_zephyr_thread_start)

    return (mp_uint_t)th->id;
}

// Create thread (standard API)
mp_uint_t mp_thread_create(void *(*entry)(void *), void *arg, size_t *stack_size) {
    char name[16];
    snprintf(name, sizeof(name), "mp_thread_%d", mp_thread_counter);
    return mp_thread_create_ex(entry, arg, stack_size, MP_THREAD_PRIORITY, name);
}

// Mark thread as finished (called by thread before exit)
void mp_thread_finish(void) {
    mp_thread_mutex_lock(&thread_mutex, 1);

    for (mp_thread_t *th = thread; th != NULL; th = th->next) {
        if (th->id == k_current_get()) {
            th->status = MP_THREAD_STATUS_FINISHED;
            DEBUG_printf("Finishing thread %s\n", k_thread_name_get(th->id));
            break;
        }
    }

    mp_thread_mutex_unlock(&thread_mutex);
}

// Initialize mutex - use Zephyr's k_mutex (recursive by default)
void mp_thread_mutex_init(mp_thread_mutex_t *mutex) {
    k_mutex_init(&mutex->handle);
}

// Lock mutex
int mp_thread_mutex_lock(mp_thread_mutex_t *mutex, int wait) {
    int ret = k_mutex_lock(&mutex->handle, wait ? K_FOREVER : K_NO_WAIT);
    return ret == 0;  // Return 1 on success, 0 on failure
}

// Unlock mutex
void mp_thread_mutex_unlock(mp_thread_mutex_t *mutex) {
    k_mutex_unlock(&mutex->handle);
    // Note: Do NOT call k_yield() here - it can cause crashes during thread
    // creation/destruction and the Zephyr scheduler will handle preemption
}

// Recursive mutex functions (for GC and memory allocation)
// Zephyr's k_mutex is recursive by default, so these are the same as regular mutex

void mp_thread_recursive_mutex_init(mp_thread_recursive_mutex_t *mutex) {
    k_mutex_init(&mutex->handle);
}

int mp_thread_recursive_mutex_lock(mp_thread_recursive_mutex_t *mutex, int wait) {
    int ret = k_mutex_lock(&mutex->handle, wait ? K_FOREVER : K_NO_WAIT);
    return ret == 0;  // Return 1 on success, 0 on failure
}

void mp_thread_recursive_mutex_unlock(mp_thread_recursive_mutex_t *mutex) {
    k_mutex_unlock(&mutex->handle);
}

// Helper: Thread iteration callback for GC
static void mp_thread_iterate_threads_cb(const struct k_thread *z_thread, void *user_data) {
    for (mp_thread_t *th = thread; th != NULL; th = th->next) {
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
