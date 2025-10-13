/*
 * This file is part of the MicroPython project, http://micropython.org/
 *
 * SPDX-License-Identifier: Apache-2.0
 *
 * Wrapper for zephyr/sys/atomic.h
 *
 * This wrapper provides atomic type definitions and function declarations.
 * The actual implementations are provided by extmod/zephyr_ble/hal/zephyr_ble_atomic.h.
 */

#ifndef MP_ZEPHYR_SYS_ATOMIC_WRAPPER_H_
#define MP_ZEPHYR_SYS_ATOMIC_WRAPPER_H_

#include <stdbool.h>
#include <stdint.h>

// Include only the type definitions
#include_next <zephyr/sys/atomic_types.h>

// Atomic bitmap macros
// Note: We define these directly to avoid including util.h which pulls in kernel.h
#define ATOMIC_BITS (sizeof(atomic_val_t) * 8)
#define ATOMIC_BITMAP_SIZE(num_bits) (((num_bits) + ATOMIC_BITS - 1) / ATOMIC_BITS)
#define ATOMIC_DEFINE(name, num_bits) atomic_t name[ATOMIC_BITMAP_SIZE(num_bits)]

// Include our atomic implementations (static inline functions)
// This must come after type definitions
#include "../../extmod/zephyr_ble/hal/zephyr_ble_atomic.h"

#endif /* MP_ZEPHYR_SYS_ATOMIC_WRAPPER_H_ */
