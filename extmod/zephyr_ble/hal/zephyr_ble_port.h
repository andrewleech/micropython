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

// Port interface for Zephyr BLE integration.
// Declares functions that ports must implement (or use weak defaults from
// zephyr_ble_port_stubs.c) and shared utility functions from zephyr_ble_poll.c.

#ifndef MICROPY_INCLUDED_EXTMOD_ZEPHYR_BLE_HAL_ZEPHYR_BLE_PORT_H
#define MICROPY_INCLUDED_EXTMOD_ZEPHYR_BLE_HAL_ZEPHYR_BLE_PORT_H

#include <stdbool.h>
#include <stdint.h>

// Forward declaration (defined in py/runtime.h)
struct _mp_sched_node_t;
typedef struct _mp_sched_node_t mp_sched_node_t;

// --- Port-provided (must implement) ---

// Called early during mp_bluetooth_init() to set up port-specific state
// (e.g. soft timer, linker references).
void mp_bluetooth_zephyr_port_init(void);

// Called during mp_bluetooth_deinit() to tear down port-specific state.
void mp_bluetooth_zephyr_port_deinit(void);

// Schedule the next HCI poll in `ms` milliseconds via port's soft timer.
void mp_bluetooth_zephyr_port_poll_in_ms(uint32_t ms);

// Top-level HCI poll entry point. Called by soft timer / mpbthciport.
void mp_bluetooth_hci_poll(void);

// Schedule immediate HCI poll (safe from PendSV / interrupt context).
void mp_bluetooth_hci_poll_now(void);

// --- Port-provided, called from k_sem_take wait loop ---

// Process HCI transport while waiting for a semaphore.
void mp_bluetooth_zephyr_hci_uart_wfi(void);

// Process queued HCI packets (called from zephyr_ble_poll.c polling path).
void mp_bluetooth_zephyr_hci_uart_process(void);

// --- GATT bump allocator (zephyr_ble_gatt_alloc.c) ---

// Reset the GATT memory pool for next init cycle.
// Only available when MICROPY_BLUETOOTH_ZEPHYR_GATT_POOL=1.
void mp_bluetooth_zephyr_gatt_pool_reset(void);

// --- Shared infrastructure in zephyr_ble_poll.c ---

// Schedule immediate BLE processing via sched_node (safe from PendSV/IRQ).
void mp_bluetooth_zephyr_port_poll_now(void);

// --- Weak defaults (override if needed) ---
// Defaults in zephyr_ble_poll.c (soft timer) and zephyr_ble_port_stubs.c (HCI RX task).

// Main task function called by sched_node. Override to add port-specific
// HCI reading before/after mp_bluetooth_zephyr_poll().
void mp_bluetooth_zephyr_port_run_task(mp_sched_node_t *node);

// Start/stop dedicated HCI RX task (FreeRTOS builds only).
void mp_bluetooth_zephyr_hci_rx_task_start(void);
void mp_bluetooth_zephyr_hci_rx_task_stop(void);

// Returns true if the HCI RX task is actively running.
bool mp_bluetooth_zephyr_hci_rx_task_active(void);

// Debug counters for HCI RX task polling.
void mp_bluetooth_zephyr_hci_rx_task_debug(uint32_t *polls, uint32_t *packets);

// Number of packets dropped due to full HCI RX queue.
uint32_t mp_bluetooth_zephyr_hci_rx_queue_dropped(void);

#endif // MICROPY_INCLUDED_EXTMOD_ZEPHYR_BLE_HAL_ZEPHYR_BLE_PORT_H
