/*
 * Zephyr util.h wrapper for MicroPython
 */

#ifndef ZEPHYR_SYS_UTIL_H_
#define ZEPHYR_SYS_UTIL_H_

#include <stdint.h>
#include <stddef.h>

// Common utility macros used by Zephyr BLE

#define ARRAY_SIZE(array) (sizeof(array) / sizeof((array)[0]))

#define CONTAINER_OF(ptr, type, field) \
    ((type *)(((char *)(ptr)) - offsetof(type, field)))

#define MIN(a, b) (((a) < (b)) ? (a) : (b))
#define MAX(a, b) (((a) > (b)) ? (a) : (b))
#define CLAMP(val, low, high) (((val) < (low)) ? (low) : (((val) > (high)) ? (high) : (val)))

#define BIT(n) (1UL << (n))
#define BIT_MASK(n) (BIT(n) - 1UL)
#define BIT64(n) (1ULL << (n))
#define BIT64_MASK(n) (BIT64(n) - 1ULL)

#define GENMASK(h, l) (((~0UL) - (1UL << (l)) + 1) & (~0UL >> (31 - (h))))

#define IS_POWER_OF_TWO(x) (((x) != 0) && (((x) & ((x) - 1)) == 0))
#define IS_ALIGNED(ptr, align) (((uintptr_t)(ptr)) % (align) == 0)

#define ROUND_UP(x, align) \
    ((((unsigned long)(x)) + ((unsigned long)(align)) - 1UL) & ~((unsigned long)(align) - 1UL))
#define ROUND_DOWN(x, align) \
    (((unsigned long)(x)) & ~((unsigned long)(align) - 1UL))

#define DIV_ROUND_UP(n, d) (((n) + (d) - 1) / (d))
#define DIV_ROUND_CLOSEST(n, d) (((n) + ((d) / 2)) / (d))

#define FIELD_GET(mask, reg) (((reg) & (mask)) >> (__builtin_ctzl(mask)))
#define FIELD_PREP(mask, val) (((val) << (__builtin_ctzl(mask))) & (mask))

// Zero/set memory
#define ZERO(var) memset(&(var), 0, sizeof(var))

// Compile-time assertions
#define BUILD_ASSERT(cond, msg) _Static_assert(cond, msg)
#define __ASSERT(test, fmt, ...) \
    do { \
        if (!(test)) { \
            printk("ASSERTION FAIL: " fmt "\n",##__VA_ARGS__); \
        } \
    } while (0)

#define __ASSERT_NO_MSG(test) __ASSERT(test, "")

// Attributes
#define __packed __attribute__((__packed__))
#define __aligned(x) __attribute__((__aligned__(x)))
#define __used __attribute__((__used__))
#define __unused __attribute__((__unused__))
#define __maybe_unused __attribute__((__unused__))
#define __deprecated __attribute__((__deprecated__))
#define __weak __attribute__((__weak__))

// Popcount
static inline unsigned int u32_count_trailing_zeros(uint32_t val) {
    return __builtin_ctz(val);
}

static inline unsigned int u64_count_trailing_zeros(uint64_t val) {
    return __builtin_ctzll(val);
}

// For/while loop helpers
#define LISTIFY(n, f, sep, ...) /* Not needed for BLE stack */

#endif /* ZEPHYR_SYS_UTIL_H_ */
