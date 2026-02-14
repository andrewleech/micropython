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

#include "py/runtime.h"
#include "shared/runtime/softtimer.h"
#include "extmod/modbluetooth.h"

#include "zephyr_ble_poll.h"
#include "zephyr_ble_port.h"
#include "zephyr_ble_timer.h"
#include "zephyr_ble_work.h"

#include <zephyr/net_buf.h>
#include <zephyr/bluetooth/buf.h>

// Weak default implementation
__attribute__((weak))
void mp_bluetooth_zephyr_hci_uart_process(void) {
    // No-op if not implemented yet
}

// --- Shared soft timer and scheduler node ---
// Used by all ports for periodic HCI polling. Ports override
// mp_bluetooth_zephyr_port_run_task() to add port-specific HCI reading.

static soft_timer_entry_t mp_zephyr_hci_soft_timer = {0};
static mp_sched_node_t mp_zephyr_hci_sched_node = {0};

// Timer callback — runs at PendSV level, schedules sched_node for main context.
static void mp_zephyr_hci_soft_timer_callback(soft_timer_entry_t *self) {
    mp_bluetooth_zephyr_port_poll_now();
}

// Schedule immediate BLE processing via sched_node.
// Safe to call from PendSV/interrupt context.
void mp_bluetooth_zephyr_port_poll_now(void) {
    mp_sched_schedule_node(&mp_zephyr_hci_sched_node, mp_bluetooth_zephyr_port_run_task);
}

// Weak default task: just runs the shared poll function.
// Ports override this to add HCI transport reading, event sorting, etc.
__attribute__((weak))
void mp_bluetooth_zephyr_port_run_task(mp_sched_node_t *node) {
    (void)node;
    mp_bluetooth_zephyr_poll();
}

// Initialise the shared soft timer for periodic HCI polling.
// Called by the weak default port_init and by port overrides.
void mp_bluetooth_zephyr_poll_init_timer(void) {
    soft_timer_static_init(
        &mp_zephyr_hci_soft_timer,
        SOFT_TIMER_MODE_ONE_SHOT,
        0,
        mp_zephyr_hci_soft_timer_callback
        );
}

// Weak default port_init: just initialise the shared soft timer.
__attribute__((weak))
void mp_bluetooth_zephyr_port_init(void) {
    mp_bluetooth_zephyr_poll_init_timer();
}

// Weak default port_poll_in_ms: reschedule the shared soft timer.
__attribute__((weak))
void mp_bluetooth_zephyr_port_poll_in_ms(uint32_t ms) {
    soft_timer_reinsert(&mp_zephyr_hci_soft_timer, ms);
}

// Stop the shared soft timer (without clearing sched_node state).
// Called from HCI transport close to stop further timer-driven polling,
// while the sched_node may still need to fire for pending work.
void mp_bluetooth_zephyr_poll_stop_timer(void) {
    soft_timer_remove(&mp_zephyr_hci_soft_timer);
}

// Clean up shared soft timer and sched_node state.
// Called by the weak default port_deinit and by port overrides.
void mp_bluetooth_zephyr_poll_cleanup(void) {
    soft_timer_remove(&mp_zephyr_hci_soft_timer);
    // Clear the scheduler node callback to prevent execution after deinit.
    // The scheduler queue persists across soft reset, and scheduler.c has a
    // safety check that skips NULL callbacks.
    mp_zephyr_hci_sched_node.callback = NULL;
}

// Weak default port_deinit: just clean up timer/sched.
__attribute__((weak))
void mp_bluetooth_zephyr_port_deinit(void) {
    mp_bluetooth_zephyr_poll_cleanup();
}

// --- Weak defaults for port HCI polling ---
// Ports override these when they need transport-specific behaviour
// (e.g. STM32 IPCC event sorting, CYW43 SPI reads).

// Weak default: delegates to port_run_task and reschedules via Zephyr timer.
// The mpbthciport timer triggers the first call, then this reschedules itself.
__attribute__((weak))
void mp_bluetooth_hci_poll(void) {
    if (mp_bluetooth_is_active()) {
        mp_bluetooth_zephyr_port_run_task(NULL);
        mp_bluetooth_zephyr_port_poll_in_ms(10);
    }
}

// Weak default: process HCI during k_sem_take wait loops.
// Prevents deadlock when main task is blocked waiting for HCI response.
__attribute__((weak))
void mp_bluetooth_zephyr_hci_uart_wfi(void) {
    mp_bluetooth_zephyr_port_run_task(NULL);
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
    // Process HCI UART first (if implemented)
    // This receives incoming HCI packets from controller and queues rx_work
    // MUST be done before work processing so callbacks fire in the same poll cycle
    mp_bluetooth_zephyr_hci_uart_process();

    // Process timers (k_timer, k_work_delayable)
    // This fires expired timers and may enqueue work items
    mp_bluetooth_zephyr_timer_process();

    // Process work queues (k_work)
    // This executes pending work handlers including rx_work from HCI events
    // SKIP if we're already processing HCI events to prevent re-entrancy
    // (poll can be called from k_sem_take→hci_uart_wfi→run_zephyr_hci_task)
    if (mp_bluetooth_zephyr_hci_processing_depth == 0) {
        mp_bluetooth_zephyr_work_process();
    }

    // Note: Rescheduling is handled by port's mp_bluetooth_zephyr_port_poll_in_ms()
}

// Check if Zephyr BT buffer pools have free buffers available.
// Returns true if at least one buffer can be allocated without blocking.
bool mp_bluetooth_zephyr_buffers_available(void) {
    // Try to allocate a buffer with K_NO_WAIT to test availability
    // If successful, immediately free it and return true
    struct net_buf *buf = bt_buf_get_rx(BT_BUF_EVT, K_NO_WAIT);
    if (buf) {
        net_buf_unref(buf);
        return true;
    }
    return false;
}
