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

#ifndef MICROPY_INCLUDED_EXTMOD_ZEPHYR_BLE_HAL_ZEPHYR_BLE_ATOMIC_H
#define MICROPY_INCLUDED_EXTMOD_ZEPHYR_BLE_HAL_ZEPHYR_BLE_ATOMIC_H

#include <stdint.h>
#include <stdbool.h>

// Include MicroPython config to get port-defined MICROPY_PY_BLUETOOTH_ENTER/EXIT
// This must come before our fallback definitions below
#include "py/mpconfig.h"

// Include Zephyr's real atomic types
// These define atomic_t, atomic_val_t from sys/atomic_types.h
#include <zephyr/sys/atomic_types.h>

// Zephyr atomic operations and spinlock abstraction for MicroPython
// Maps to port-defined MICROPY_PY_BLUETOOTH_ENTER/EXIT macros

// Port should define these macros in mpconfigport.h:
// MICROPY_PY_BLUETOOTH_ENTER - Enter critical section, return atomic_state
// MICROPY_PY_BLUETOOTH_EXIT  - Exit critical section, restore atomic_state

// Provide default no-op implementations if port doesn't define them
// This allows compilation to succeed, but is not safe for multi-core or IRQ contexts
// Ports should define these in mpconfigport.h for proper critical section handling
#ifndef MICROPY_PY_BLUETOOTH_ENTER
#define MICROPY_PY_BLUETOOTH_ENTER uint32_t atomic_state = 0; (void)atomic_state;
#endif

#ifndef MICROPY_PY_BLUETOOTH_EXIT
#define MICROPY_PY_BLUETOOTH_EXIT (void)atomic_state;
#endif

// --- Spinlock API ---

struct k_spinlock {
    // Empty structure, actual locking done by MICROPY_PY_BLUETOOTH_ENTER/EXIT
    uint8_t unused;
};

typedef uint32_t k_spinlock_key_t;

// Lock spinlock and enter critical section
static inline k_spinlock_key_t k_spin_lock(struct k_spinlock *lock) {
    (void)lock;
    MICROPY_PY_BLUETOOTH_ENTER
    return atomic_state;
}

// Unlock spinlock and exit critical section
static inline void k_spin_unlock(struct k_spinlock *lock, k_spinlock_key_t key) {
    (void)lock;
    uint32_t atomic_state = key;
    MICROPY_PY_BLUETOOTH_EXIT
}

// --- Atomic Operations ---

// Atomic types are now provided by zephyr/sys/atomic_types.h:
// - atomic_t (long int)
// - atomic_val_t (atomic_t)
// - atomic_ptr_t (void *)
// - atomic_ptr_val_t (atomic_ptr_t)
//
// Note: We provide our own implementations of atomic functions below
// to avoid pulling in Zephyr's atomic_builtin.h which conflicts.

// Atomic pointer initializer macro
// Since atomic_ptr_t is just void*, initialization is direct
#define ATOMIC_PTR_INIT(val) (val)

// Initialize atomic variable
static inline void atomic_set(atomic_t *target, atomic_val_t value) {
    MICROPY_PY_BLUETOOTH_ENTER
    *target = value;
    MICROPY_PY_BLUETOOTH_EXIT
}

// Clear atomic variable (set to 0)
// Returns: void
static inline void atomic_clear(atomic_t *target) {
    atomic_set(target, 0);
}

// Get atomic variable value
static inline atomic_val_t atomic_get(const atomic_t *target) {
    MICROPY_PY_BLUETOOTH_ENTER
    atomic_val_t ret = *target;
    MICROPY_PY_BLUETOOTH_EXIT
    return ret;
}

// Atomic increment and return OLD value (before increment)
// Note: Zephyr's atomic_inc returns the old value, not the new value!
static inline atomic_val_t atomic_inc(atomic_t *target) {
    MICROPY_PY_BLUETOOTH_ENTER
    atomic_val_t ret = (*target)++;
    MICROPY_PY_BLUETOOTH_EXIT
    return ret;
}

// Atomic decrement and return OLD value (before decrement)
// Note: Zephyr's atomic_dec returns the old value, not the new value!
// This is critical for reference counting (e.g., bt_conn_unref checks old > 0)
static inline atomic_val_t atomic_dec(atomic_t *target) {
    MICROPY_PY_BLUETOOTH_ENTER
    atomic_val_t ret = (*target)--;
    MICROPY_PY_BLUETOOTH_EXIT
    return ret;
}

// Atomic add and return new value
static inline atomic_val_t atomic_add(atomic_t *target, atomic_val_t value) {
    MICROPY_PY_BLUETOOTH_ENTER
    *target += value;
    atomic_val_t ret = *target;
    MICROPY_PY_BLUETOOTH_EXIT
    return ret;
}

// Atomic subtract and return new value
static inline atomic_val_t atomic_sub(atomic_t *target, atomic_val_t value) {
    MICROPY_PY_BLUETOOTH_ENTER
    *target -= value;
    atomic_val_t ret = *target;
    MICROPY_PY_BLUETOOTH_EXIT
    return ret;
}

// Atomic compare and swap
static inline bool atomic_cas(atomic_t *target, atomic_val_t old_value, atomic_val_t new_value) {
    MICROPY_PY_BLUETOOTH_ENTER
    bool ret = false;
    if (*target == old_value) {
        *target = new_value;
        ret = true;
    }
    MICROPY_PY_BLUETOOTH_EXIT
    return ret;
}

// Atomic bitwise OR
static inline atomic_val_t atomic_or(atomic_t *target, atomic_val_t value) {
    MICROPY_PY_BLUETOOTH_ENTER
    atomic_val_t ret = *target;
    *target |= value;
    MICROPY_PY_BLUETOOTH_EXIT
    return ret;
}

// Atomic bitwise AND
static inline atomic_val_t atomic_and(atomic_t *target, atomic_val_t value) {
    MICROPY_PY_BLUETOOTH_ENTER
    atomic_val_t ret = *target;
    *target &= value;
    MICROPY_PY_BLUETOOTH_EXIT
    return ret;
}

// Atomic test and set bit
static inline bool atomic_test_and_set_bit(atomic_t *target, int bit) {
    MICROPY_PY_BLUETOOTH_ENTER
    bool ret = (*target & (1UL << bit)) != 0;
    *target |= (1UL << bit);
    MICROPY_PY_BLUETOOTH_EXIT
    return ret;
}

// Atomic test and clear bit
static inline bool atomic_test_and_clear_bit(atomic_t *target, int bit) {
    MICROPY_PY_BLUETOOTH_ENTER
    bool ret = (*target & (1UL << bit)) != 0;
    *target &= ~(1UL << bit);
    MICROPY_PY_BLUETOOTH_EXIT
    return ret;
}

// Atomic test bit
static inline bool atomic_test_bit(const atomic_t *target, int bit) {
    MICROPY_PY_BLUETOOTH_ENTER
    bool ret = (*target & (1UL << bit)) != 0;
    MICROPY_PY_BLUETOOTH_EXIT
    return ret;
}

// Atomic set bit
static inline void atomic_set_bit(atomic_t *target, int bit) {
    MICROPY_PY_BLUETOOTH_ENTER
    *target |= (1UL << bit);
    MICROPY_PY_BLUETOOTH_EXIT
}

// Atomic clear bit
static inline void atomic_clear_bit(atomic_t *target, int bit) {
    MICROPY_PY_BLUETOOTH_ENTER
    *target &= ~(1UL << bit);
    MICROPY_PY_BLUETOOTH_EXIT
}

// Atomic set bit to specific value
static inline void atomic_set_bit_to(atomic_t *target, int bit, bool val) {
    if (val) {
        atomic_set_bit(target, bit);
    } else {
        atomic_clear_bit(target, bit);
    }
}

// Atomic pointer operations
// Note: atomic_set_ptr returns old value (exchange operation)
static inline void *atomic_ptr_set(atomic_ptr_t *target, void *value) {
    MICROPY_PY_BLUETOOTH_ENTER
    void *old_val = *target;
    *target = value;
    MICROPY_PY_BLUETOOTH_EXIT
    return old_val;
}

// Non-returning version
static inline void atomic_set_ptr(atomic_ptr_t *target, void *value) {
    MICROPY_PY_BLUETOOTH_ENTER
    *target = value;
    MICROPY_PY_BLUETOOTH_EXIT
}

static inline void *atomic_get_ptr(const atomic_ptr_t *target) {
    MICROPY_PY_BLUETOOTH_ENTER
    void *ret = *target;
    MICROPY_PY_BLUETOOTH_EXIT
    return ret;
}

// Alias for consistency with Zephyr naming (both forms used in BLE stack)
static inline void *atomic_ptr_get(const atomic_ptr_t *target) {
    return atomic_get_ptr(target);
}

static inline bool atomic_cas_ptr(atomic_ptr_t *target, void *old_value, void *new_value) {
    MICROPY_PY_BLUETOOTH_ENTER
    bool ret = false;
    if (*target == old_value) {
        *target = new_value;
        ret = true;
    }
    MICROPY_PY_BLUETOOTH_EXIT
    return ret;
}

// Alias for consistency with Zephyr naming (both forms used in BLE stack)
static inline bool atomic_ptr_cas(atomic_ptr_t *target, void *old_value, void *new_value) {
    return atomic_cas_ptr(target, old_value, new_value);
}

// Atomic pointer clear (set to NULL) and return old value
static inline void *atomic_ptr_clear(atomic_ptr_t *target) {
    MICROPY_PY_BLUETOOTH_ENTER
    void *old_val = *target;
    *target = NULL;
    MICROPY_PY_BLUETOOTH_EXIT
    return old_val;
}

// --- IRQ Lock API (alternative to spinlocks) ---

typedef unsigned int irq_lock_key_t;

// Lock IRQs and return key.
// When the controller runs on-core, ISRs access shared data structures so
// irq_lock must actually disable hardware interrupts via PRIMASK.
// For host-only builds (CYW43, IPCC) the no-op MICROPY_PY_BLUETOOTH_ENTER
// is sufficient since there are no local BLE ISRs.
static inline irq_lock_key_t irq_lock(void) {
    #if MICROPY_BLUETOOTH_ZEPHYR_CONTROLLER && (defined(__arm__) || defined(__thumb__))
    unsigned int key;
    __asm volatile ("mrs %0, primask" : "=r" (key));
    __asm volatile ("cpsid i" ::: "memory");
    return key;
    #else
    MICROPY_PY_BLUETOOTH_ENTER
    return atomic_state;
    #endif
}

// Unlock IRQs with key
static inline void irq_unlock(irq_lock_key_t key) {
    #if MICROPY_BLUETOOTH_ZEPHYR_CONTROLLER && (defined(__arm__) || defined(__thumb__))
    __asm volatile ("msr primask, %0" :: "r" (key) : "memory");
    #else
    uint32_t atomic_state = key;
    MICROPY_PY_BLUETOOTH_EXIT
    #endif
}

#endif // MICROPY_INCLUDED_EXTMOD_ZEPHYR_BLE_HAL_ZEPHYR_BLE_ATOMIC_H
