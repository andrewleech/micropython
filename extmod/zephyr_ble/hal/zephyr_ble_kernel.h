/*
 * This file is part of the MicroPython project, http://micropython.org/
 *
 * The MIT License (MIT)
 *
 * Copyright (c) 2025 MicroPython Contributors
 *
 * Permission is hereby granted, free of charge, to any person obtaining a copy
 * of this software and associated documentation files (the "Software"), to deal
 * in the Software without restriction, including without limitation the rights
 * to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
 * copies of the Software, and to permit persons to whom the Software is
 * furnished to do so, subject to the following conditions:
 *
 * The above copyright notice and this permission notice shall be included in
 * all copies or substantial portions of the Software.
 *
 * THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
 * IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
 * FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
 * AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
 * LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
 * OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
 * THE SOFTWARE.
 */

#ifndef MICROPY_INCLUDED_EXTMOD_ZEPHYR_BLE_HAL_ZEPHYR_BLE_KERNEL_H
#define MICROPY_INCLUDED_EXTMOD_ZEPHYR_BLE_HAL_ZEPHYR_BLE_KERNEL_H

#include "zephyr_ble_work.h"
#include "py/mphal.h"

// Zephyr kernel miscellaneous abstractions for MicroPython
// Maps k_sleep, k_yield, k_uptime_get, etc. to MicroPython functions

// --- Sleep and Yield ---

// Sleep for specified timeout
void k_sleep(k_timeout_t timeout);

// Yield CPU (allow other tasks to run)
// Note: In MicroPython's cooperative scheduler, explicit yielding isn't needed
// since the BLE stack runs in scheduled tasks that automatically yield when complete.
// MICROPY_EVENT_POLL_HOOK can't be used here due to linkage issues with inline functions.
static inline void k_yield(void) {
    // No-op in MicroPython scheduler
}

// Busy wait for short delays (microseconds)
static inline void k_busy_wait(uint32_t usec_to_wait) {
    mp_hal_delay_us(usec_to_wait);
}

// --- Uptime and Timing ---

// Get system uptime in milliseconds
static inline int64_t k_uptime_get(void) {
    return (int64_t)mp_hal_ticks_ms();
}

// Get system uptime in 32-bit milliseconds (wraps at ~49 days)
static inline uint32_t k_uptime_get_32(void) {
    return mp_hal_ticks_ms();
}

// Convert uptime to ticks (1:1 mapping in MicroPython, both are milliseconds)
static inline int64_t k_uptime_ticks(void) {
    return (int64_t)mp_hal_ticks_ms();
}

// Get current cycle count (if available, otherwise return uptime in ms)
static inline uint32_t k_cycle_get_32(void) {
    #ifdef mp_hal_ticks_cpu
    return mp_hal_ticks_cpu();
    #else
    return mp_hal_ticks_ms();
    #endif
}

// --- Thread Info (No-op in MicroPython) ---

// Thread ID type
typedef void *k_tid_t;

// Get current thread ID (returns &k_sys_work_q.thread in MicroPython)
// CRITICAL: Must return &k_sys_work_q.thread so that bt_hci_cmd_send_sync()
// comparison at hci_core.c:478 succeeds and enters the synchronous command
// processing path. Without this, commands are queued but never processed,
// causing deadlock at k_sem_take().
static inline k_tid_t k_current_get(void) {
    extern struct k_work_q k_sys_work_q;
    return (k_tid_t)&k_sys_work_q.thread;
}

// Check if in ISR context (always returns false in MicroPython scheduler)
static inline bool k_is_in_isr(void) {
    return false;
}

// Check if pre-emptible (always returns false, no preemption in scheduler)
static inline bool k_is_preempt_thread(void) {
    return false;
}

// --- System State ---

// Kernel version info (stub values)
#define KERNEL_VERSION_MAJOR 3
#define KERNEL_VERSION_MINOR 7
#define KERNEL_VERSION_PATCHLEVEL 0

// System state (always running in MicroPython)
static inline bool k_is_pre_kernel(void) {
    return false;
}

// --- Fatal Error Handlers ---

// Fatal error handler - triggers a panic/abort
// Used by BT_ASSERT when CONFIG_BT_ASSERT_PANIC is enabled
NORETURN void k_panic(void);

// Recoverable error handler - logs error but continues
// Used by BT_ASSERT when CONFIG_BT_ASSERT_PANIC is disabled
void k_oops(void);

#endif // MICROPY_INCLUDED_EXTMOD_ZEPHYR_BLE_HAL_ZEPHYR_BLE_KERNEL_H
