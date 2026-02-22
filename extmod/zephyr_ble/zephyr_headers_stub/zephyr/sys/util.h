/*
 * This file is part of the MicroPython project, http://micropython.org/
 *
 * SPDX-License-Identifier: Apache-2.0
 *
 * Wrapper for zephyr/sys/util.h
 *
 * Fixes CONTAINER_OF to work with empty BUILD_ASSERT.
 * Handles macro redefinition warnings between zephyr_ble_config.h and real util.h.
 */

#ifndef MP_ZEPHYR_SYS_UTIL_WRAPPER_H_
#define MP_ZEPHYR_SYS_UTIL_WRAPPER_H_

#include <stddef.h>  // for offsetof

// Undef macros that may conflict with real util.h definitions
// These may come from zephyr_ble_config.h, nrfx, or pico-sdk headers
#undef BITS_PER_BYTE
#undef FLEXIBLE_ARRAY_DECLARE
#undef CONTAINER_OF
#undef ARRAY_SIZE
// Pico-SDK defines KHZ/MHZ as constants, Zephyr defines them as function-like macros
#undef KHZ
#undef MHZ

// Suppress macro redefinition warnings for unavoidable conflicts
// between our stubs and real Zephyr headers
#pragma GCC diagnostic push
#pragma GCC diagnostic ignored "-Wcpp"

// Include the real util.h for other macros (ROUND_UP, ARRAY_SIZE, etc.)
#include_next <zephyr/sys/util.h>

#pragma GCC diagnostic pop

// Zephyr's CONTAINER_OF uses CONTAINER_OF_VALIDATE which contains BUILD_ASSERT.
// Since we've made BUILD_ASSERT empty, CONTAINER_OF_VALIDATE becomes invalid.
// Override with simple version without validation.
#undef CONTAINER_OF_VALIDATE
#define CONTAINER_OF_VALIDATE(ptr, type, field)

#undef CONTAINER_OF
#define CONTAINER_OF(_ptr, _type, _field) \
    ((_type *)(((char *)(_ptr)) - offsetof(_type, _field)))

#endif /* MP_ZEPHYR_SYS_UTIL_WRAPPER_H_ */
