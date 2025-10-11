/*
 * Zephyr sys/util.h wrapper for MicroPython
 * Utility macros and functions
 */

// Use the same header guard as real Zephyr sys/util.h to prevent conflicts
#ifndef ZEPHYR_INCLUDE_SYS_UTIL_H_
#define ZEPHYR_INCLUDE_SYS_UTIL_H_

#include <stddef.h>
#include <stdint.h>
#include <stdbool.h>
#include <string.h>

// Include MicroPython's utilities for MP_ARRAY_SIZE
#include "py/misc.h"

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

// ARRAY_SIZE - Use MicroPython's proven macro to avoid duplication
#ifndef ARRAY_SIZE
#define ARRAY_SIZE MP_ARRAY_SIZE
#endif

// Note: ARRAY_SIZE stub function is provided in zephyr_ble_array_size_stub.c
// but not forward-declared here to avoid macro expansion conflicts.
// The linker will resolve it when needed (zero-sized array edge cases).

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

// Pointer to integer conversion
#ifndef POINTER_TO_UINT
#define POINTER_TO_UINT(x) ((uintptr_t)(x))
#endif

#ifndef UINT_TO_POINTER
#define UINT_TO_POINTER(x) ((void *)(uintptr_t)(x))
#endif

// Hex string to binary conversion
static inline size_t hex2bin(const char *hex, size_t hexlen, uint8_t *buf, size_t buflen) {
    if (hexlen / 2 > buflen) {
        return 0;
    }

    for (size_t i = 0; i < hexlen / 2; i++) {
        char hi = hex[i * 2];
        char lo = hex[i * 2 + 1];

        uint8_t hi_val, lo_val;
        if (hi >= '0' && hi <= '9') hi_val = hi - '0';
        else if (hi >= 'a' && hi <= 'f') hi_val = hi - 'a' + 10;
        else if (hi >= 'A' && hi <= 'F') hi_val = hi - 'A' + 10;
        else return 0;

        if (lo >= '0' && lo <= '9') lo_val = lo - '0';
        else if (lo >= 'a' && lo <= 'f') lo_val = lo - 'a' + 10;
        else if (lo >= 'A' && lo <= 'F') lo_val = lo - 'A' + 10;
        else return 0;

        buf[i] = (hi_val << 4) | lo_val;
    }

    return hexlen / 2;
}

// Convert uint8_t to decimal string
uint8_t u8_to_dec(char *buf, uint8_t buflen, uint8_t value);

// Memory comparison utility
static inline bool util_memeq(const void *a, const void *b, size_t len) {
    return memcmp(a, b, len) == 0;
}

// Byte swap utility - reverse byte order in place
static inline void sys_mem_swap(void *buf, size_t length) {
    uint8_t *p = (uint8_t *)buf;
    for (size_t i = 0; i < length / 2; i++) {
        uint8_t tmp = p[i];
        p[i] = p[length - 1 - i];
        p[length - 1 - i] = tmp;
    }
}

#endif /* ZEPHYR_INCLUDE_SYS_UTIL_H_ */
