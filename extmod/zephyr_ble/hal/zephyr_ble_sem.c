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

#if ZEPHYR_BLE_DEBUG
#define DEBUG_SEM_printf(...) mp_printf(&mp_plat_print, "SEM: " __VA_ARGS__)
#else
// CRITICAL: Keep SEM debug printfs enabled even when ZEPHYR_BLE_DEBUG=0.
// These printfs provide necessary timing delays for the IPCC hardware and scheduler
// to complete HCI response processing. Without these delays, k_sem_take() times out
// before HCI responses are fully processed and delivered to waiting semaphores.
#define DEBUG_SEM_printf(...) mp_printf(&mp_plat_print, "SEM: " __VA_ARGS__)
#endif

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

    // ARCHITECTURAL FIX for Issue #6 recursion deadlock:
    // Set the wait loop flag to allow work processing from within this wait context.
    // This prevents deadlock when work queue handler blocks waiting for HCI command response.
    // See docs/BLE_TIMING_ARCHITECTURE.md for detailed analysis.
    extern volatile bool mp_bluetooth_zephyr_in_wait_loop;
    mp_bluetooth_zephyr_in_wait_loop = true;

    while (sem->count == 0) {
        // Process HCI packets FIRST, before checking timeout
        // This ensures any pending responses are processed immediately
        // mp_bluetooth_zephyr_hci_uart_wfi() now directly processes HCI packets
        // and allows work queue processing via the in_wait_loop flag
        mp_bluetooth_zephyr_hci_uart_wfi();

        // Check timeout AFTER processing HCI packets
        uint32_t elapsed = mp_hal_ticks_ms() - t0;
        if (timeout_ms != 0xFFFFFFFF && elapsed >= timeout_ms) {
            DEBUG_SEM_printf("  --> timeout after %u ms\n", elapsed);
            mp_bluetooth_zephyr_in_wait_loop = false;
            return -EAGAIN;
        }

        // Minimal yield to prevent busy-waiting while allowing rapid HCI processing
        // No delay needed since HCI processing already includes 100μs delay
        if (timeout_ms == 0xFFFFFFFF) {
            mp_event_wait_indefinite();
        } else {
            // Just yield without delay for maximum responsiveness
            mp_event_wait_ms(0);
        }
    }

    // Clear the wait loop flag before returning
    mp_bluetooth_zephyr_in_wait_loop = false;

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
