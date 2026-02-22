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


// --- Uptime and Timing ---


// --- Thread Info (No-op in MicroPython) ---

// Thread ID type
typedef void *k_tid_t;

// Get current thread ID
// Returns &k_sys_work_q.thread when executing work from the system work queue
// This allows Zephyr's hci_core.c to correctly detect it's in work queue context
// and use the synchronous command sending path (process_pending_cmd).
static inline k_tid_t k_current_get(void) {
    extern struct k_work_q k_sys_work_q;
    // Cooperative polling: all code runs in the main thread which acts as
    // the system work queue thread. Returning this pointer makes Zephyr's
    // hci_core.c detect work-queue context for synchronous HCI commands.
    return (k_tid_t)&k_sys_work_q.thread;
}

// Check if in ISR context.
// For on-core controller builds, check the ARM IPSR register to detect real
// ISR context. The controller's mayfly system uses this (via mayfly_is_running)
// to decide whether to execute work inline or queue it.
// For host-only builds, always returns false (no real ISRs).
static inline bool k_is_in_isr(void) {
    #if MICROPY_BLUETOOTH_ZEPHYR_CONTROLLER && defined(CONFIG_CPU_CORTEX_M)
    uint32_t ipsr;
    __asm__ volatile ("mrs %0, ipsr" : "=r" (ipsr));
    return (ipsr & 0x1FF) != 0;
    #else
    return false;
    #endif
}


// --- System State ---

// Kernel version info (stub values)
#define KERNEL_VERSION_MAJOR 3
#define KERNEL_VERSION_MINOR 7
#define KERNEL_VERSION_PATCHLEVEL 0


// --- Fatal Error Handlers ---

// Fatal error handler - triggers a panic/abort
// Used by BT_ASSERT when CONFIG_BT_ASSERT_PANIC is enabled
NORETURN void k_panic(void);

// Recoverable error handler - logs error but continues
// Used by BT_ASSERT when CONFIG_BT_ASSERT_PANIC is disabled
void k_oops(void);

#endif // MICROPY_INCLUDED_EXTMOD_ZEPHYR_BLE_HAL_ZEPHYR_BLE_KERNEL_H
