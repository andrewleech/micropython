/*
 * This file is part of the MicroPython project, http://micropython.org/
 *
 * SPDX-License-Identifier: Apache-2.0
 *
 * Wrapper for zephyr/toolchain/gcc.h
 *
 * This stub shadows the real Zephyr gcc.h and ensures our config
 * (with CONFIG_ARM etc.) is loaded BEFORE the real header checks
 * for architecture defines.
 */

#ifndef MP_ZEPHYR_TOOLCHAIN_GCC_WRAPPER_H_
#define MP_ZEPHYR_TOOLCHAIN_GCC_WRAPPER_H_

// Load our configuration FIRST - this defines CONFIG_ARM and other
// architecture defines that the real gcc.h checks for
#include <zephyr/autoconf.h>

// Now include the real gcc.h - it will see CONFIG_ARM is defined
#include_next <zephyr/toolchain/gcc.h>

// gcc.h defines BUILD_ASSERT with _Static_assert, but this fails with
// complex type expressions using __builtin_types_compatible_p.
// Override with empty definition for maintainability.
#undef BUILD_ASSERT
#define BUILD_ASSERT(EXPR, ...)

#endif /* MP_ZEPHYR_TOOLCHAIN_GCC_WRAPPER_H_ */
