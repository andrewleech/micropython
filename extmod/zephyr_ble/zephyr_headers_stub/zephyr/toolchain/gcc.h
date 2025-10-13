/*
 * This file is part of the MicroPython project, http://micropython.org/
 *
 * SPDX-License-Identifier: Apache-2.0
 *
 * Wrapper for zephyr/toolchain/gcc.h
 *
 * Includes the real gcc.h but overrides BUILD_ASSERT to avoid
 * complex type expression issues with _Static_assert.
 */

#ifndef MP_ZEPHYR_TOOLCHAIN_GCC_WRAPPER_H_
#define MP_ZEPHYR_TOOLCHAIN_GCC_WRAPPER_H_

// Include the real gcc.h
#include_next <zephyr/toolchain/gcc.h>

// gcc.h defines BUILD_ASSERT with _Static_assert, but this fails with
// complex type expressions using __builtin_types_compatible_p.
// Override with empty definition for maintainability.
#undef BUILD_ASSERT
#define BUILD_ASSERT(EXPR, ...)

#endif /* MP_ZEPHYR_TOOLCHAIN_GCC_WRAPPER_H_ */
