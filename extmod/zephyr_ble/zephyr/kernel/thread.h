/*
 * Zephyr kernel/thread.h wrapper for MicroPython
 * Thread APIs - no-op stubs (no threading in MicroPython BLE)
 */

#ifndef ZEPHYR_KERNEL_THREAD_H_
#define ZEPHYR_KERNEL_THREAD_H_

#include <stdint.h>

// Thread structure (minimal stub)
struct k_thread {
    int dummy;
};

// Thread ID type
typedef struct k_thread *k_tid_t;

// Get current thread (always returns NULL - no threads)
static inline k_tid_t k_current_get(void) {
    return NULL;
}

// Thread name functions (no-op)
static inline const char *k_thread_name_get(k_tid_t thread) {
    (void)thread;
    return "bt";
}

static inline int k_thread_name_set(k_tid_t thread, const char *name) {
    (void)thread;
    (void)name;
    return 0;
}

// Thread state (not used)
static inline const char *k_thread_state_str(k_tid_t thread) {
    (void)thread;
    return "running";
}

#endif /* ZEPHYR_KERNEL_THREAD_H_ */
