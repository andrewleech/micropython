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

#include "py/mpconfig.h"
#include "py/mphal.h"
#include "py/runtime.h"
#include "py/misc.h"
#include "zephyr_ble_sem.h"
#include "zephyr_ble_work.h"
#include "zephyr_ble_port.h"

#include <stddef.h>
#include <stdio.h>

// Maximum time to poll before returning -EAGAIN. Caps K_FOREVER to prevent
// infinite hang if HCI transport is broken or during deinit.
// Must be short enough that nested k_sem_take calls (from work handlers
// blocking inside poll→work_process→k_sem_take→hci_uart_wfi→poll recursion)
// don't exceed the test runner's 10-second timeout. With depth-2 nesting,
// 1000ms per level = 2 seconds total.
#define ZEPHYR_BLE_SEM_POLL_TIMEOUT_MS 1000

#if ZEPHYR_BLE_DEBUG
#define DEBUG_SEM_printf(...) mp_printf(&mp_plat_print, "SEM: " __VA_ARGS__)
#else
#define DEBUG_SEM_printf(...) do {} while (0)
#endif

// mp_bluetooth_zephyr_hci_uart_wfi is declared in zephyr_ble_poll.h
// and has a weak default in zephyr_ble_poll.c that calls port_run_task().

void k_sem_init(struct k_sem *sem, unsigned int initial_count, unsigned int limit) {
    DEBUG_SEM_printf("k_sem_init(%p, count=%u, limit=%u)\n", sem, initial_count, limit);
    sem->count = initial_count;
    sem->limit = limit;
}

int k_sem_take(struct k_sem *sem, k_timeout_t timeout) {
    DEBUG_SEM_printf("k_sem_take(%p, timeout=%u, caller=%p)\n", sem, timeout.ticks, __builtin_return_address(0));

    // Fast path: semaphore available (protected against concurrent k_sem_give
    // from HCI RX processing context)
    {
        MICROPY_PY_BLUETOOTH_ENTER
        if (sem->count > 0) {
            sem->count--;
            MICROPY_PY_BLUETOOTH_EXIT
            DEBUG_SEM_printf("  --> fast path, count now %u\n", sem->count);
            return 0;
        }
        MICROPY_PY_BLUETOOTH_EXIT
    }

    // K_NO_WAIT: Don't block
    if (timeout.ticks == 0) {
        DEBUG_SEM_printf("  --> no wait, returning EBUSY\n");
        return -EBUSY;
    }

    // During BLE deinit, fail K_FOREVER waits immediately. Stale work handlers
    // (from dead connections) would block on semaphores that will never be signaled.
    // bt_disable() uses K_SECONDS(10) not K_FOREVER, so its own HCI_RESET wait
    // is unaffected. Uses deiniting flag (not shutting_down) because poll_uart
    // must still work during bt_disable for HCI transport.
    extern volatile bool mp_bluetooth_zephyr_deiniting;
    if (mp_bluetooth_zephyr_deiniting && timeout.ticks == 0xFFFFFFFF) {
        return -EAGAIN;
    }

    // K_FOREVER: Block indefinitely (capped to prevent infinite hang)
    // Regular timeout: Block for specified time
    uint32_t t0 = mp_hal_ticks_ms();
    uint32_t timeout_ms = (timeout.ticks == 0xFFFFFFFF) ? ZEPHYR_BLE_SEM_POLL_TIMEOUT_MS : timeout.ticks;
    if (timeout_ms > ZEPHYR_BLE_SEM_POLL_TIMEOUT_MS) {
        timeout_ms = ZEPHYR_BLE_SEM_POLL_TIMEOUT_MS;
    }

    DEBUG_SEM_printf("  --> waiting (timeout=%u ms, caller=%p)\n", timeout_ms, __builtin_return_address(0));

    // Set wait loop flag to allow work processing
    mp_bluetooth_zephyr_in_wait_loop = true;

    while (sem->count == 0) {
        // Process HCI packets
        mp_bluetooth_zephyr_hci_uart_wfi();

        // Check for pending MicroPython exception (e.g. KeyboardInterrupt from Ctrl-C).
        // Return -EAGAIN so Zephyr callers treat this as a timeout and clean up normally.
        // The exception remains in mp_pending_exception and is raised once control
        // returns to the Python VM.
        if (MP_STATE_THREAD(mp_pending_exception) != MP_OBJ_NULL) {
            DEBUG_SEM_printf("  --> interrupted by pending exception\n");
            mp_bluetooth_zephyr_in_wait_loop = false;
            return -EAGAIN;
        }

        // Check timeout
        uint32_t elapsed = mp_hal_ticks_ms() - t0;
        if (elapsed >= timeout_ms) {
            DEBUG_SEM_printf("  --> timeout after %u ms\n", elapsed);
            mp_bluetooth_zephyr_in_wait_loop = false;
            return -EAGAIN;
        }

        // Process port-level events (HCI RX polling on unix, no-op on embedded
        // where the soft timer interrupt handles it).
        MICROPY_INTERNAL_EVENT_HOOK;
        // Process MicroPython scheduled callbacks without raising exceptions.
        // Uses CALLBACKS_ONLY to avoid nlr_raise which would unwind past
        // Zephyr's C frames without cleanup.
        mp_handle_pending(MP_HANDLE_PENDING_CALLBACKS_ONLY);
        MICROPY_INTERNAL_WFE(10);
    }

    mp_bluetooth_zephyr_in_wait_loop = false;

    // Semaphore became available
    MICROPY_PY_BLUETOOTH_ENTER
    sem->count--;
    MICROPY_PY_BLUETOOTH_EXIT
    DEBUG_SEM_printf("  --> acquired after %u ms, count now %u\n",
        mp_hal_ticks_ms() - t0, sem->count);
    return 0;
}

void k_sem_give(struct k_sem *sem) {
    DEBUG_SEM_printf("k_sem_give(%p)\n", sem);

    MICROPY_PY_BLUETOOTH_ENTER
    if (sem->count < sem->limit) {
        sem->count++;
        MICROPY_PY_BLUETOOTH_EXIT
        DEBUG_SEM_printf("  --> count now %u\n", sem->count);
    } else {
        MICROPY_PY_BLUETOOTH_EXIT
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
