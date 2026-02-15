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

// No-op implementations for HAL/port functions called by modbluetooth_zephyr.c.
// On native Zephyr, the real kernel handles work queues, timers, HCI transport,
// and net_buf pools.

#include "py/mpconfig.h"

#if MICROPY_PY_BLUETOOTH && MICROPY_BLUETOOTH_ZEPHYR

#include <zephyr/kernel.h>
#include "extmod/zephyr_ble/hal/zephyr_ble_work.h"
#include "extmod/zephyr_ble/hal/zephyr_ble_port.h"
#include "extmod/zephyr_ble/hal/zephyr_ble_poll.h"

// --- Globals referenced by zephyr_ble_work.h ---
volatile bool mp_bluetooth_zephyr_in_wait_loop = false;
volatile int mp_bluetooth_zephyr_hci_processing_depth = 0;

// --- Work queue (Zephyr's own work thread handles processing) ---
void mp_bluetooth_zephyr_work_process(void) {
}

void mp_bluetooth_zephyr_work_process_init(void) {
}

void mp_bluetooth_zephyr_init_phase_enter(void) {
}

void mp_bluetooth_zephyr_init_phase_exit(void) {
}

bool mp_bluetooth_zephyr_in_init_phase(void) {
    return false;
}

bool mp_bluetooth_zephyr_init_work_pending(void) {
    return false;
}

struct k_work *mp_bluetooth_zephyr_init_work_get(void) {
    return NULL;
}

void mp_bluetooth_zephyr_work_thread_start(void) {
}

void mp_bluetooth_zephyr_work_thread_stop(void) {
}

bool mp_bluetooth_zephyr_work_drain(void) {
    return false;
}

void mp_bluetooth_zephyr_work_reset(void) {
}

void mp_bluetooth_zephyr_work_debug_stats(void) {
}

// --- Port hooks ---
void mp_bluetooth_zephyr_port_init(void) {
}

void mp_bluetooth_zephyr_port_deinit(void) {
}

void mp_bluetooth_zephyr_port_poll_in_ms(uint32_t ms) {
    (void)ms;
}

// --- HCI (Zephyr handles HCI transport internally) ---
void mp_bluetooth_hci_poll(void) {
}

void mp_bluetooth_hci_poll_now(void) {
}

void mp_bluetooth_zephyr_hci_uart_wfi(void) {
}

void mp_bluetooth_zephyr_hci_uart_process(void) {
}

// --- Polling subsystem (not needed, Zephyr is event-driven) ---
void mp_bluetooth_zephyr_poll(void) {
}

void mp_bluetooth_zephyr_poll_init(void) {
}

void mp_bluetooth_zephyr_poll_deinit(void) {
}

bool mp_bluetooth_zephyr_buffers_available(void) {
    return true;
}

void mp_bluetooth_zephyr_poll_init_timer(void) {
}

void mp_bluetooth_zephyr_poll_stop_timer(void) {
}

void mp_bluetooth_zephyr_poll_cleanup(void) {
}

// --- Net buf pool (Zephyr manages pools) ---
void mp_net_buf_pool_state_reset(void) {
}

// --- GATT pool (not using bump allocator on native Zephyr) ---
void mp_bluetooth_zephyr_gatt_pool_reset(void) {
}

// --- Timer processing (Zephyr kernel handles timers) ---
void mp_bluetooth_zephyr_timer_process(void) {
}

// --- HCI RX task stubs (Zephyr handles HCI reception internally) ---
void mp_bluetooth_zephyr_hci_rx_task_start(void) {
}

void mp_bluetooth_zephyr_hci_rx_task_stop(void) {
}

bool mp_bluetooth_zephyr_hci_rx_task_active(void) {
    return false;
}

void mp_bluetooth_zephyr_hci_rx_task_debug(uint32_t *polls, uint32_t *packets) {
    if (polls) {
        *polls = 0;
    }
    if (packets) {
        *packets = 0;
    }
}

uint32_t mp_bluetooth_zephyr_hci_rx_queue_dropped(void) {
    return 0;
}

#endif // MICROPY_PY_BLUETOOTH && MICROPY_BLUETOOTH_ZEPHYR
