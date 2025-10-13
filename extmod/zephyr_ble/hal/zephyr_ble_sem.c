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

#include "py/mphal.h"
#include "py/runtime.h"
#include "py/misc.h"
#include "zephyr_ble_sem.h"
#include "zephyr_ble_work.h"

#include <stddef.h>
#include <stdio.h>

#define DEBUG_SEM_printf(...) // mp_printf(&mp_plat_print, __VA_ARGS__)

// Forward declaration of HCI UART processing function
// This will be implemented when we integrate the HCI layer
extern void mp_bluetooth_zephyr_hci_uart_wfi(void) __attribute__((weak));

// Weak default implementation
void mp_bluetooth_zephyr_hci_uart_wfi(void) {
    // No-op if not implemented yet
}

// --- Semaphore API ---

void k_sem_init(struct k_sem *sem, unsigned int initial_count, unsigned int limit) {
    DEBUG_SEM_printf("k_sem_init(%p, count=%u, limit=%u)\n", sem, initial_count, limit);
    sem->count = initial_count;
    sem->limit = limit;
}

int k_sem_take(struct k_sem *sem, k_timeout_t timeout) {
    DEBUG_SEM_printf("k_sem_take(%p, timeout=%u)\n", sem, timeout.ticks);

    // Fast path: semaphore available
    if (sem->count > 0) {
        sem->count--;
        DEBUG_SEM_printf("  --> fast path, count now %u\n", sem->count);
        return 0;
    }

    // K_NO_WAIT: Don't block
    if (timeout.ticks == 0) {
        DEBUG_SEM_printf("  --> no wait, returning EBUSY\n");
        return -EBUSY;
    }

    // K_FOREVER: Block indefinitely
    // Regular timeout: Block for specified time
    uint32_t t0 = mp_hal_ticks_ms();
    uint32_t timeout_ms = (timeout.ticks == 0xFFFFFFFF) ? 0xFFFFFFFF : timeout.ticks;

    DEBUG_SEM_printf("  --> waiting (timeout=%u ms)\n", timeout_ms);

    // Busy-wait pattern (like NimBLE's ble_npl_sem_pend)
    while (sem->count == 0) {
        // Check timeout (handles wrap-around correctly using unsigned arithmetic)
        uint32_t elapsed = mp_hal_ticks_ms() - t0;
        if (timeout_ms != 0xFFFFFFFF && elapsed >= timeout_ms) {
            DEBUG_SEM_printf("  --> timeout after %u ms\n", elapsed);
            return -EAGAIN;
        }

        // Process pending work (keeps BLE stack responsive)
        mp_bluetooth_zephyr_work_process();

        // Process HCI UART (if implemented)
        mp_bluetooth_zephyr_hci_uart_wfi();

        // Yield to prevent tight loop
        #ifdef MICROPY_EVENT_POLL_HOOK
        MICROPY_EVENT_POLL_HOOK
        #endif
    }

    // Semaphore became available
    sem->count--;
    DEBUG_SEM_printf("  --> acquired after %u ms, count now %u\n",
        mp_hal_ticks_ms() - t0, sem->count);
    return 0;
}

void k_sem_give(struct k_sem *sem) {
    DEBUG_SEM_printf("k_sem_give(%p)\n", sem);

    if (sem->count < sem->limit) {
        sem->count++;
        DEBUG_SEM_printf("  --> count now %u\n", sem->count);
    } else {
        DEBUG_SEM_printf("  --> at limit, not incrementing\n");
    }
}

unsigned int k_sem_count_get(struct k_sem *sem) {
    return sem->count;
}

void k_sem_reset(struct k_sem *sem) {
    DEBUG_SEM_printf("k_sem_reset(%p)\n", sem);
    sem->count = 0;
}
