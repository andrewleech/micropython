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

#ifndef MICROPY_INCLUDED_EXTMOD_ZEPHYR_BLE_HAL_ZEPHYR_BLE_POLL_H
#define MICROPY_INCLUDED_EXTMOD_ZEPHYR_BLE_HAL_ZEPHYR_BLE_POLL_H

// Main polling function for Zephyr BLE stack
// Called periodically by mp_bluetooth_hci_poll() in port implementations

#include <stdbool.h>

// Process all pending timers, work queues, and HCI UART
void mp_bluetooth_zephyr_poll(void);

// Initialize polling subsystem
void mp_bluetooth_zephyr_poll_init(void);

// Deinitialize polling subsystem
void mp_bluetooth_zephyr_poll_deinit(void);

// Check if Zephyr BT buffer pools have free buffers available.
// Returns true if at least one buffer can be allocated without blocking.
// This prevents silent packet drops when buffer pool is exhausted.
bool mp_bluetooth_zephyr_buffers_available(void);

// Initialise the shared soft timer for periodic HCI polling.
// Port overrides of mp_bluetooth_zephyr_port_init() should call this
// before doing port-specific setup.
void mp_bluetooth_zephyr_poll_init_timer(void);

// Stop the shared soft timer without clearing sched_node state.
// Called from HCI transport close to halt timer-driven polling.
void mp_bluetooth_zephyr_poll_stop_timer(void);

// Clean up shared soft timer and sched_node state.
// Port overrides of mp_bluetooth_zephyr_port_deinit() should call this
// to handle the timer/sched cleanup before doing port-specific teardown.
void mp_bluetooth_zephyr_poll_cleanup(void);

#endif // MICROPY_INCLUDED_EXTMOD_ZEPHYR_BLE_HAL_ZEPHYR_BLE_POLL_H
