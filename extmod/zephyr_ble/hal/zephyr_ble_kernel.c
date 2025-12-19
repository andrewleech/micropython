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

#include "zephyr_ble_kernel.h"
#include "py/mphal.h"
#include <zephyr/device.h>

// --- Sleep ---

void k_sleep(k_timeout_t timeout) {
    // K_FOREVER is not supported for sleep (would block indefinitely)
    if (timeout.ticks == 0xFFFFFFFF) {
        // Just yield instead
        k_yield();
        return;
    }

    // K_NO_WAIT just yields
    if (timeout.ticks == 0) {
        k_yield();
        return;
    }

    // Sleep for specified milliseconds
    mp_hal_delay_ms(timeout.ticks);
}

// --- Scheduler Lock/Unlock ---

void k_sched_lock(void) {
    // No-op in cooperative scheduler
    // All code runs in main context, no preemption
}

void k_sched_unlock(void) {
    // No-op in cooperative scheduler
    // All code runs in main context, no preemption
}

// --- Device Readiness Check ---
// Note: device_is_ready() is now provided as a static inline function
// in zephyr_headers_stub/zephyr/device.h

// --- Fatal Error Handlers ---

#include "py/runtime.h"

// For debugging, we'll track the panic location
static const char *panic_file = NULL;
static unsigned int panic_line = 0;

// Called by __ASSERT_POST_ACTION from Zephyr assert macros
NORETURN void assert_post_action(const char *file, unsigned int line) {
    panic_file = file;
    panic_line = line;
    k_panic();
}

NORETURN void k_panic(void) {
    // Fatal error in BLE stack - report location if available
    if (panic_file) {
        mp_printf(&mp_plat_print, "ASSERT FAILED at %s:%d\n", panic_file, panic_line);
    } else {
        mp_printf(&mp_plat_print, "BLE k_panic (location unknown)\n");
    }
    mp_raise_msg(&mp_type_RuntimeError, MP_ERROR_TEXT("BLE stack fatal error (k_panic)"));
}

void k_oops(void) {
    // Recoverable error in BLE stack - log and continue
    // In MicroPython, we can't easily continue after an assertion failure,
    // so we raise an exception but mark it as non-fatal
    mp_printf(&mp_plat_print, "WARNING: BLE stack assertion failed (k_oops)\n");
}
