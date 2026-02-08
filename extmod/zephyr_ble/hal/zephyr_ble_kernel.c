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

// For bt_dev ncmd_sem debugging
#if MICROPY_PY_NETWORK_CYW43
#include "hci_core.h"
#endif

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
#include <stdarg.h>

// For debugging, we'll track the panic location
static const char *panic_file = NULL;
static unsigned int panic_line = 0;

// Called by __ASSERT_PRINT from Zephyr assert macros when CONFIG_ASSERT_VERBOSE is enabled
// This overrides the weak definition in lib/zephyr/lib/os/assert.c
void assert_print(const char *fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    // Use vprintf since mp_vprintf is not readily available
    vprintf(fmt, ap);
    va_end(ap);
}

// Called by __ASSERT_POST_ACTION from Zephyr assert macros
NORETURN void assert_post_action(const char *file, unsigned int line) {
    panic_file = file;
    panic_line = line;
    // Print assertion location using mp_printf for reliable output in all contexts
    mp_printf(&mp_plat_print, "\n*** ASSERT FAILED: %s:%u ***\n", file, line);
    k_panic();
}

NORETURN void k_panic(void) {
    // Fatal error in BLE stack - print debug info before raising exception
    #if MICROPY_PY_NETWORK_CYW43
    // Debug counters from mpzephyrport_rp2.c / mphalport.c / mpnetworkport.c
    extern volatile uint32_t poll_uart_count;
    extern volatile uint32_t poll_uart_hci_reads;
    extern volatile uint32_t poll_uart_skipped_recursion;
    extern volatile uint32_t poll_uart_skipped_no_cb;
    extern volatile uint32_t poll_uart_cyw43_calls;
    extern volatile uint32_t hci_tx_count;
    extern volatile uint32_t hci_tx_cmd_count;
    extern volatile uint32_t cyw43_bt_hci_process_count;
    extern void mp_bluetooth_zephyr_hci_rx_task_debug(uint32_t *polls, uint32_t *packets);
    // HCI RX validation counters
    extern volatile uint32_t hci_rx_total_processed;
    extern volatile uint32_t hci_rx_rejected_len;
    extern volatile uint32_t hci_rx_rejected_param_len;
    extern volatile uint32_t hci_rx_rejected_oversize;
    extern volatile uint32_t hci_rx_rejected_event;
    extern volatile uint32_t hci_rx_rejected_acl;
    extern volatile uint32_t hci_rx_rejected_type;
    extern volatile uint32_t hci_rx_buf_failed;

    // Get ncmd_sem count from bt_dev structure
    extern struct bt_dev bt_dev;
    unsigned int ncmd_count = k_sem_count_get(&bt_dev.ncmd_sem);
    void *ncmd_sem_addr = &bt_dev.ncmd_sem;

    uint32_t rx_task_polls = 0, rx_task_packets = 0;
    mp_bluetooth_zephyr_hci_rx_task_debug(&rx_task_polls, &rx_task_packets);

    mp_printf(&mp_plat_print, "\n=== k_panic Debug Info ===\n");
    if (panic_file) {
        mp_printf(&mp_plat_print, "Assert location: %s:%u\n", panic_file, panic_line);
    }
    mp_printf(&mp_plat_print, "ncmd_sem: %p count=%u\n", ncmd_sem_addr, ncmd_count);
    mp_printf(&mp_plat_print, "poll_uart: calls=%lu cyw43=%lu hci_reads=%lu\n",
           (unsigned long)poll_uart_count, (unsigned long)poll_uart_cyw43_calls,
           (unsigned long)poll_uart_hci_reads);
    mp_printf(&mp_plat_print, "poll_uart skipped: recursion=%lu no_cb=%lu\n",
           (unsigned long)poll_uart_skipped_recursion, (unsigned long)poll_uart_skipped_no_cb);
    mp_printf(&mp_plat_print, "HCI: tx=%lu tx_cmd=%lu bt_process=%lu\n",
           (unsigned long)hci_tx_count, (unsigned long)hci_tx_cmd_count,
           (unsigned long)cyw43_bt_hci_process_count);
    extern uint32_t mp_bluetooth_zephyr_hci_rx_queue_dropped(void);
    uint32_t queue_dropped = mp_bluetooth_zephyr_hci_rx_queue_dropped();
    mp_printf(&mp_plat_print, "HCI RX task: polls=%lu packets=%lu queue_dropped=%lu\n",
           (unsigned long)rx_task_polls, (unsigned long)rx_task_packets, (unsigned long)queue_dropped);
    mp_printf(&mp_plat_print, "HCI RX: total=%lu rejected: len=%lu param=%lu size=%lu evt=%lu acl=%lu type=%lu buf=%lu\n",
           (unsigned long)hci_rx_total_processed,
           (unsigned long)hci_rx_rejected_len, (unsigned long)hci_rx_rejected_param_len,
           (unsigned long)hci_rx_rejected_oversize, (unsigned long)hci_rx_rejected_event,
           (unsigned long)hci_rx_rejected_acl, (unsigned long)hci_rx_rejected_type,
           (unsigned long)hci_rx_buf_failed);
    mp_printf(&mp_plat_print, "==========================\n");
    #endif

    // Raise Python exception to allow cleanup
    mp_raise_msg(&mp_type_RuntimeError, MP_ERROR_TEXT("BLE stack fatal error (k_panic)"));
}

void k_oops(void) {
    // Recoverable error in BLE stack
    // NOTE: Cannot use mp_printf here - may be called from work thread without NLR context
    // In MicroPython, we treat this as non-fatal but log nothing to avoid thread safety issues
    // The calling code will handle the error condition
}
