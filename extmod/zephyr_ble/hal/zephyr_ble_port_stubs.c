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

// Weak default implementations for port-specific HCI RX task functions.
// Ports that use a dedicated HCI RX task (e.g. RP2 FreeRTOS variant)
// provide strong overrides. All other builds use these no-op defaults.

#include <stdbool.h>
#include <stdint.h>

// --- HCI RX Task Stubs ---

__attribute__((weak))
void mp_bluetooth_zephyr_hci_rx_task_start(void) {
    // No-op: port uses polling mode
}

__attribute__((weak))
void mp_bluetooth_zephyr_hci_rx_task_stop(void) {
    // No-op
}

__attribute__((weak))
bool mp_bluetooth_zephyr_hci_rx_task_active(void) {
    return false;
}

__attribute__((weak))
void mp_bluetooth_zephyr_hci_rx_task_debug(uint32_t *polls, uint32_t *packets) {
    if (polls) {
        *polls = 0;
    }
    if (packets) {
        *packets = 0;
    }
}

__attribute__((weak))
uint32_t mp_bluetooth_zephyr_hci_rx_queue_dropped(void) {
    return 0;
}
