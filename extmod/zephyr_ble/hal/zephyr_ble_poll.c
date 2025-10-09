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

#include "zephyr_ble_poll.h"
#include "zephyr_ble_timer.h"
#include "zephyr_ble_work.h"

// Forward declaration of HCI UART processing function
// This will be implemented when we integrate the HCI layer
extern void mp_bluetooth_zephyr_hci_uart_process(void) __attribute__((weak));

// Weak default implementation
void mp_bluetooth_zephyr_hci_uart_process(void) {
    // No-op if not implemented yet
}

// --- Polling Functions ---

void mp_bluetooth_zephyr_poll_init(void) {
    // Currently nothing to initialize
    // Timers and work queues are initialized on first use
}

void mp_bluetooth_zephyr_poll_deinit(void) {
    // Currently nothing to deinitialize
    // In the future, this might clean up global state
}

void mp_bluetooth_zephyr_poll(void) {
    // Process timers (k_timer, k_work_delayable)
    // This fires expired timers and may enqueue work items
    mp_bluetooth_zephyr_timer_process();

    // Process work queues (k_work)
    // This executes pending work handlers
    mp_bluetooth_zephyr_work_process();

    // Process HCI UART (if implemented)
    // This handles incoming HCI packets from controller
    mp_bluetooth_zephyr_hci_uart_process();

    // Note: Rescheduling is handled by port's mp_bluetooth_hci_poll_in_ms()
    // Port should call mp_bluetooth_hci_poll_in_ms(128) after this function
}
