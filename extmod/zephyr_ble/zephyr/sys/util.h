/*
 * Zephyr sys/util.h wrapper for MicroPython
 * Utility macros and functions
 */

#ifndef ZEPHYR_SYS_UTIL_H_
#define ZEPHYR_SYS_UTIL_H_

#include <stddef.h>
#include <stdint.h>

// Many of these are already defined in kernel.h, but include them here too

// Container-of macro (also in kernel.h)
#ifndef CONTAINER_OF
#define CONTAINER_OF(ptr, type, field) \
    ((type *)(((char *)(ptr)) - offsetof(type, field)))
#endif

#ifndef MIN
#define MIN(a, b) (((a) < (b)) ? (a) : (b))
#endif

#ifndef MAX
#define MAX(a, b) (((a) > (b)) ? (a) : (b))
#endif

#ifndef ARRAY_SIZE
#define ARRAY_SIZE(array) (sizeof(array) / sizeof((array)[0]))
#endif

#ifndef BIT
#define BIT(n) (1UL << (n))
#endif

#ifndef BIT_MASK
#define BIT_MASK(n) (BIT(n) - 1UL)
#endif

// Round up/down to power of 2
#define ROUND_UP(x, align) \
    ((((x) + ((align) - 1)) / (align)) * (align))
#define ROUND_DOWN(x, align) \
    (((x) / (align)) * (align))

// Check if value is power of 2
#define IS_POWER_OF_TWO(x) (((x) != 0) && (((x) & ((x) - 1)) == 0))

// Ceiling of integer division
#define DIV_ROUND_UP(n, d) (((n) + (d) - 1) / (d))

// Absolute value
#ifndef ABS
#define ABS(x) ((x) < 0 ? -(x) : (x))
#endif

// Swap bytes
#define BSWAP_16(x) \
    ((uint16_t)(((x) << 8) | ((x) >> 8)))
#define BSWAP_32(x) \
    ((uint32_t)((((x) & 0xFF000000) >> 24) | \
                (((x) & 0x00FF0000) >> 8) | \
                (((x) & 0x0000FF00) << 8) | \
                (((x) & 0x000000FF) << 24)))

// Pointer alignment
#define IS_ALIGNED(ptr, align) \
    (((uintptr_t)(ptr) & ((align) - 1)) == 0)

// For-each macros (basic versions)
#define FOR_EACH(F, sep, ...)
#define FOR_EACH_IDX(F, sep, ...)

#endif /* ZEPHYR_SYS_UTIL_H_ */
