/*
 * This file is part of the MicroPython project, http://micropython.org/
 *
 * SPDX-License-Identifier: Apache-2.0
 *
 * Wrapper for zephyr/sys/util.h
 *
 * Fixes CONTAINER_OF to work with empty BUILD_ASSERT.
 */

#ifndef MP_ZEPHYR_SYS_UTIL_WRAPPER_H_
#define MP_ZEPHYR_SYS_UTIL_WRAPPER_H_

#include <stddef.h>  // for offsetof

// Include the real util.h for other macros (ROUND_UP, ARRAY_SIZE, etc.)
#include_next <zephyr/sys/util.h>

// Zephyr's CONTAINER_OF uses CONTAINER_OF_VALIDATE which contains BUILD_ASSERT.
// Since we've made BUILD_ASSERT empty, CONTAINER_OF_VALIDATE becomes invalid.
// Override with simple version without validation.
#undef CONTAINER_OF_VALIDATE
#define CONTAINER_OF_VALIDATE(ptr, type, field)

#undef CONTAINER_OF
#define CONTAINER_OF(_ptr, _type, _field) \
    ((_type *)(((char *)(_ptr)) - offsetof(_type, _field)))

#endif /* MP_ZEPHYR_SYS_UTIL_WRAPPER_H_ */
