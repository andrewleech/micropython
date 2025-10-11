/*
 * Zephyr sys_clock.h wrapper for MicroPython
 * Time conversion constants and clock functions
 */

#ifndef ZEPHYR_SYS_CLOCK_H_
#define ZEPHYR_SYS_CLOCK_H_

#include <stdint.h>
#include "../hal/zephyr_ble_kernel.h"

// =============================================================================
// Time Conversion Constants
// =============================================================================
// These define the relationships between different time units.
// Used throughout the BLE stack for timeout calculations and conversions.
//
// Design Note: Using ULL suffix ensures 64-bit arithmetic even on 32-bit
// platforms, preventing overflow in calculations like (ms * USEC_PER_MSEC).

// Base conversions
#define NSEC_PER_USEC 1000ULL
#define USEC_PER_MSEC 1000ULL
#ifndef MSEC_PER_SEC
#define MSEC_PER_SEC  1000ULL
#endif

// Derived conversions (computed from base for consistency)
#define USEC_PER_SEC  (USEC_PER_MSEC * MSEC_PER_SEC)     // 1,000,000
#define NSEC_PER_MSEC (NSEC_PER_USEC * USEC_PER_MSEC)    // 1,000,000
#define NSEC_PER_SEC  (NSEC_PER_MSEC * MSEC_PER_SEC)     // 1,000,000,000

// =============================================================================
// Tick Conversion Functions
// =============================================================================
// Zephyr Semantic: System time measured in "ticks" where tick rate is
// configurable (CONFIG_SYS_CLOCK_TICKS_PER_SEC, typically 1000-10000 Hz).
//
// MicroPython Mapping: 1 tick = 1 millisecond for simplicity.
// This matches CONFIG_SYS_CLOCK_TICKS_PER_SEC=1000 defined in config.
//
// Rationale: BLE stack operates at millisecond granularity. Finer resolution
// would complicate timing without providing measurable benefits.
//
// Note: k_ticks_t is defined in hal/zephyr_ble_work.h as uint32_t
// We use that definition to avoid conflicts

// Convert milliseconds to ticks (identity operation: 1ms = 1 tick)
static inline k_ticks_t k_ms_to_ticks_floor32(uint32_t ms) {
    return (k_ticks_t)ms;
}

static inline k_ticks_t k_ms_to_ticks_floor64(uint64_t ms) {
    return (k_ticks_t)ms;
}

// Convert microseconds to ticks (round down: 1000us = 1 tick)
static inline k_ticks_t k_us_to_ticks_floor32(uint32_t us) {
    return (k_ticks_t)(us / USEC_PER_MSEC);
}

static inline k_ticks_t k_us_to_ticks_floor64(uint64_t us) {
    return (k_ticks_t)(us / USEC_PER_MSEC);
}

// Convert ticks to milliseconds (identity operation: 1 tick = 1ms)
static inline uint32_t k_ticks_to_ms_floor32(k_ticks_t ticks) {
    return (uint32_t)ticks;
}

static inline uint64_t k_ticks_to_ms_floor64(k_ticks_t ticks) {
    return (uint64_t)ticks;
}

// Convert ticks to microseconds (1 tick = 1000us)
static inline uint32_t k_ticks_to_us_floor32(k_ticks_t ticks) {
    return (uint32_t)(ticks * USEC_PER_MSEC);
}

static inline uint64_t k_ticks_to_us_floor64(k_ticks_t ticks) {
    return (uint64_t)(ticks * USEC_PER_MSEC);
}

// k_uptime_get and related functions are in our HAL kernel.h
// This header provides time conversion constants and tick conversion functions

#endif /* ZEPHYR_SYS_CLOCK_H_ */
