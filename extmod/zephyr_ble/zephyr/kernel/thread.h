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

// Note: k_tid_t and k_current_get are defined in zephyr_ble_kernel.h
// to avoid conflicts

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

// Thread abort (no-op)
static inline void k_thread_abort(k_tid_t thread) {
    (void)thread;
}

// Thread stack definition (no-op in MicroPython)
// Note: Caller adds 'static', so don't include it here
#ifndef K_KERNEL_STACK_DEFINE
#define K_KERNEL_STACK_DEFINE(name, size) \
    uint8_t name[size]
#endif

// Thread priority macros (not used in MicroPython)
#define K_PRIO_COOP(x) (x)
#define K_PRIO_PREEMPT(x) (x)

// Thread stack size query (always returns 1 in MicroPython)
#ifndef K_THREAD_STACK_SIZEOF
#define K_THREAD_STACK_SIZEOF(sym) 1
#endif

#endif /* ZEPHYR_KERNEL_THREAD_H_ */
