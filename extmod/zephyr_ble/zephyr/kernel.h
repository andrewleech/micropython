/*
 * Zephyr kernel.h wrapper for MicroPython
 * Redirects to our HAL implementation
 */

#ifndef ZEPHYR_KERNEL_H_
#define ZEPHYR_KERNEL_H_

// Include autoconf.h first to provide CONFIG_* defines
#include "zephyr/autoconf.h"

// Include our HAL implementation
#include "../hal/zephyr_ble_hal.h"

// Include Zephyr's standard types
#include <stdint.h>
#include <stddef.h>
#include <stdbool.h>
#include <string.h>

// Error codes
#include <errno.h>

// Zephyr common macros
#define ARRAY_SIZE(array) (sizeof(array) / sizeof((array)[0]))
#define CONTAINER_OF(ptr, type, field) \
    ((type *)(((char *)(ptr)) - offsetof(type, field)))

#define MIN(a, b) (((a) < (b)) ? (a) : (b))
#define MAX(a, b) (((a) > (b)) ? (a) : (b))

#define BIT(n) (1UL << (n))
#define BIT_MASK(n) (BIT(n) - 1UL)

// Zephyr attributes
#define __packed __attribute__((__packed__))
#define __aligned(x) __attribute__((__aligned__(x)))
#define __used __attribute__((__used__))
#define __unused __attribute__((__unused__))
#define __maybe_unused __attribute__((__unused__))
#define __deprecated __attribute__((__deprecated__))

// Zephyr build assertions
#define BUILD_ASSERT(cond, msg) _Static_assert(cond, msg)

// Zephyr printk (map to printf for now)
#define printk printf

#endif /* ZEPHYR_KERNEL_H_ */
