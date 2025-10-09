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

// Zephyr atomic operations and spinlock abstraction for MicroPython
// Maps to port-defined MICROPY_PY_BLUETOOTH_ENTER/EXIT macros

// Port should define these macros in mpconfigport.h:
// MICROPY_PY_BLUETOOTH_ENTER - Enter critical section, return atomic_state
// MICROPY_PY_BLUETOOTH_EXIT  - Exit critical section, restore atomic_state

// Provide default no-op implementations if port doesn't define them
// This allows compilation to succeed, but is not safe for multi-core or IRQ contexts
#ifndef MICROPY_PY_BLUETOOTH_ENTER
#warning "MICROPY_PY_BLUETOOTH_ENTER not defined, using no-op (not IRQ-safe)"
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

// Atomic variable type
typedef struct {
    volatile int32_t val;
} atomic_t;

typedef struct {
    volatile void *val;
} atomic_ptr_t;

// Atomic value type (like atomic_val_t in Zephyr)
typedef int32_t atomic_val_t;

// Initialize atomic variable
static inline void atomic_set(atomic_t *target, atomic_val_t value) {
    MICROPY_PY_BLUETOOTH_ENTER
    target->val = value;
    MICROPY_PY_BLUETOOTH_EXIT
}

// Get atomic variable value
static inline atomic_val_t atomic_get(const atomic_t *target) {
    MICROPY_PY_BLUETOOTH_ENTER
    atomic_val_t ret = target->val;
    MICROPY_PY_BLUETOOTH_EXIT
    return ret;
}

// Atomic increment and return new value
static inline atomic_val_t atomic_inc(atomic_t *target) {
    MICROPY_PY_BLUETOOTH_ENTER
    atomic_val_t ret = ++target->val;
    MICROPY_PY_BLUETOOTH_EXIT
    return ret;
}

// Atomic decrement and return new value
static inline atomic_val_t atomic_dec(atomic_t *target) {
    MICROPY_PY_BLUETOOTH_ENTER
    atomic_val_t ret = --target->val;
    MICROPY_PY_BLUETOOTH_EXIT
    return ret;
}

// Atomic add and return new value
static inline atomic_val_t atomic_add(atomic_t *target, atomic_val_t value) {
    MICROPY_PY_BLUETOOTH_ENTER
    target->val += value;
    atomic_val_t ret = target->val;
    MICROPY_PY_BLUETOOTH_EXIT
    return ret;
}

// Atomic subtract and return new value
static inline atomic_val_t atomic_sub(atomic_t *target, atomic_val_t value) {
    MICROPY_PY_BLUETOOTH_ENTER
    target->val -= value;
    atomic_val_t ret = target->val;
    MICROPY_PY_BLUETOOTH_EXIT
    return ret;
}

// Atomic compare and swap
static inline bool atomic_cas(atomic_t *target, atomic_val_t old_value, atomic_val_t new_value) {
    MICROPY_PY_BLUETOOTH_ENTER
    bool ret = false;
    if (target->val == old_value) {
        target->val = new_value;
        ret = true;
    }
    MICROPY_PY_BLUETOOTH_EXIT
    return ret;
}

// Atomic bitwise OR
static inline atomic_val_t atomic_or(atomic_t *target, atomic_val_t value) {
    MICROPY_PY_BLUETOOTH_ENTER
    atomic_val_t ret = target->val;
    target->val |= value;
    MICROPY_PY_BLUETOOTH_EXIT
    return ret;
}

// Atomic bitwise AND
static inline atomic_val_t atomic_and(atomic_t *target, atomic_val_t value) {
    MICROPY_PY_BLUETOOTH_ENTER
    atomic_val_t ret = target->val;
    target->val &= value;
    MICROPY_PY_BLUETOOTH_EXIT
    return ret;
}

// Atomic test and set bit
static inline bool atomic_test_and_set_bit(atomic_t *target, int bit) {
    MICROPY_PY_BLUETOOTH_ENTER
    bool ret = (target->val & (1UL << bit)) != 0;
    target->val |= (1UL << bit);
    MICROPY_PY_BLUETOOTH_EXIT
    return ret;
}

// Atomic test and clear bit
static inline bool atomic_test_and_clear_bit(atomic_t *target, int bit) {
    MICROPY_PY_BLUETOOTH_ENTER
    bool ret = (target->val & (1UL << bit)) != 0;
    target->val &= ~(1UL << bit);
    MICROPY_PY_BLUETOOTH_EXIT
    return ret;
}

// Atomic test bit
static inline bool atomic_test_bit(const atomic_t *target, int bit) {
    MICROPY_PY_BLUETOOTH_ENTER
    bool ret = (target->val & (1UL << bit)) != 0;
    MICROPY_PY_BLUETOOTH_EXIT
    return ret;
}

// Atomic set bit
static inline void atomic_set_bit(atomic_t *target, int bit) {
    MICROPY_PY_BLUETOOTH_ENTER
    target->val |= (1UL << bit);
    MICROPY_PY_BLUETOOTH_EXIT
}

// Atomic clear bit
static inline void atomic_clear_bit(atomic_t *target, int bit) {
    MICROPY_PY_BLUETOOTH_ENTER
    target->val &= ~(1UL << bit);
    MICROPY_PY_BLUETOOTH_EXIT
}

// Atomic pointer operations
static inline void atomic_set_ptr(atomic_ptr_t *target, void *value) {
    MICROPY_PY_BLUETOOTH_ENTER
    target->val = value;
    MICROPY_PY_BLUETOOTH_EXIT
}

static inline void *atomic_get_ptr(const atomic_ptr_t *target) {
    MICROPY_PY_BLUETOOTH_ENTER
    void *ret = (void *)target->val;
    MICROPY_PY_BLUETOOTH_EXIT
    return ret;
}

static inline bool atomic_cas_ptr(atomic_ptr_t *target, void *old_value, void *new_value) {
    MICROPY_PY_BLUETOOTH_ENTER
    bool ret = false;
    if (target->val == old_value) {
        target->val = new_value;
        ret = true;
    }
    MICROPY_PY_BLUETOOTH_EXIT
    return ret;
}

// --- IRQ Lock API (alternative to spinlocks) ---

typedef unsigned int irq_lock_key_t;

// Lock IRQs and return key
static inline irq_lock_key_t irq_lock(void) {
    MICROPY_PY_BLUETOOTH_ENTER
    return atomic_state;
}

// Unlock IRQs with key
static inline void irq_unlock(irq_lock_key_t key) {
    uint32_t atomic_state = key;
    MICROPY_PY_BLUETOOTH_EXIT
}

#endif // MICROPY_INCLUDED_EXTMOD_ZEPHYR_BLE_HAL_ZEPHYR_BLE_ATOMIC_H
