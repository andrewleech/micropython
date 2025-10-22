/*
 * Minimal kernel_arch_func.h for Unix/POSIX Zephyr integration
 *
 * This provides architecture-specific function stubs for the Zephyr kernel
 * when building without full architecture support.
 */

#ifndef ZEPHYR_KERNEL_ARCH_FUNC_H
#define ZEPHYR_KERNEL_ARCH_FUNC_H

#include <zephyr/types.h>
#include <zephyr/sys/atomic_types.h>
#include <stdbool.h>

// Atomic operations (must be inline for use in headers like spinlock.h)
static inline bool atomic_cas(atomic_t *target, atomic_val_t old_value, atomic_val_t new_value) {
    return __atomic_compare_exchange_n(target, &old_value, new_value,
                                       0, __ATOMIC_SEQ_CST, __ATOMIC_SEQ_CST);
}

static inline atomic_val_t atomic_set(atomic_t *target, atomic_val_t value) {
    return __atomic_exchange_n(target, value, __ATOMIC_SEQ_CST);
}

struct k_thread;

// Stub: Set thread return value (not used in Unix POC)
static inline void arch_thread_return_value_set(struct k_thread *thread, unsigned int value) {
    (void)thread;
    (void)value;
}

// Kernel initialization stub
static inline void arch_kernel_init(void) {
}

// Note: arch_new_thread and arch_nop are declared in kernel_arch_interface.h
// We provide implementations in zephyr_arch_unix.c

#endif /* ZEPHYR_KERNEL_ARCH_FUNC_H */
