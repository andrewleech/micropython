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

// nRF port integration for Zephyr BLE stack with on-core controller.
// The Zephyr BLE controller runs on the same core as MicroPython, using the
// nRF52840's radio peripheral directly. No external HCI transport is needed —
// the controller's hci_driver.c provides open/close/send via bt_recv() from
// ISR context.
//
// Mayfly processing: The controller's link layer uses a mayfly (deferred work)
// system to move data between priority levels. Radio ISR → LLL → ULL_HIGH →
// ULL_LOW → host. In native Zephyr, SWI ISRs run mayfly_run() at each level.
// In our cooperative build, ISRs fire but mayfly processing may not complete
// before the next poll. We explicitly call mayfly_run() from the polling path
// to ensure all pending mayflies (especially rx_demux) are drained.

#include "py/mpconfig.h"

#if MICROPY_PY_BLUETOOTH && MICROPY_BLUETOOTH_ZEPHYR

#include "py/runtime.h"
#include "py/mphal.h"
#include "extmod/modbluetooth.h"
#include "extmod/zephyr_ble/hal/zephyr_ble_poll.h"
#include "extmod/zephyr_ble/hal/zephyr_ble_port.h"

#include "extmod/zephyr_ble/hal/zephyr_ble_hal.h"
#include "zephyr/device.h"

#if MICROPY_BLUETOOTH_ZEPHYR_CONTROLLER
// Mayfly processing for controller deferred work.
// We use extern declarations rather than including the full controller headers
// to avoid pulling in memq.h and other internal types into the port file.
extern void mayfly_run(uint8_t callee_id);

// Ticker user IDs map to mayfly callee IDs (from lll.h / mayfly.h):
//   TICKER_USER_ID_ULL_HIGH = MAYFLY_CALL_ID_1 = 1
//   TICKER_USER_ID_ULL_LOW  = MAYFLY_CALL_ID_2 = 2
#define TICKER_USER_ID_ULL_HIGH 1
#define TICKER_USER_ID_ULL_LOW  2
#endif

#if ZEPHYR_BLE_DEBUG
#define DEBUG_printf(...) mp_printf(&mp_plat_print, "NRF_BLE: " __VA_ARGS__)
#else
#define DEBUG_printf(...) do {} while (0)
#endif

// The on-core controller's hci_driver.c creates the HCI device via
// BT_HCI_CONTROLLER_INIT(0) -> DEVICE_DT_INST_DEFINE. Our stub device.h
// routes DEVICE_DT_DEFINE to create __device_dts_ord_0 with the controller's
// hci_driver_api. See extmod/zephyr_ble/zephyr_headers_stub/zephyr/device.h.
//
// If the stubs are not yet set up to handle DEVICE_DT_DEFINE from hci_driver.c,
// we provide a fallback device structure here. The controller's hci_driver_api
// is static in hci_driver.c so we cannot reference it directly — the device
// must be created by hci_driver.c itself via the DEVICE_DT_DEFINE stub.

#if !MICROPY_BLUETOOTH_ZEPHYR_CONTROLLER
// Host-only mode: port provides its own HCI device (e.g. external controller).
// Not used for nRF on-core controller builds.
static struct device_state hci_device_state = {
    .init_res = 0,
    .initialized = true,
};

__attribute__((used, externally_visible))
const struct device __device_dts_ord_0 = {
    .name = "HCI_NRF",
    .config = NULL,
    .api = NULL,  // No API — host-only mode
    .state = &hci_device_state,
    .data = NULL,
    .ops = {.init = NULL},
    .flags = 0,
};
#endif

// Process all pending controller mayflies and then poll HCI rx.
//
// The controller's link layer queues deferred work (mayflies) at different
// priority levels during radio ISRs. In native Zephyr, dedicated SWI ISRs
// run mayfly_run() at each level. In our cooperative build we must explicitly
// drain these from the polling path to ensure data flows through:
//   LLL (radio) → ULL_HIGH (rx_demux) → sem_recv → hci_driver_poll_rx
//
// Without this, rx_demux never executes and ACL data from the central
// sits in the controller's internal queue indefinitely.
static void mp_bluetooth_zephyr_controller_poll_rx(void) {
    #if MICROPY_BLUETOOTH_ZEPHYR_CONTROLLER
    // Run pending mayflies at each controller priority level.
    // Order matters: ULL_HIGH processes rx_demux which feeds sem_recv,
    // ULL_LOW handles deferred cleanup and scheduling.
    mayfly_run(TICKER_USER_ID_ULL_HIGH);
    mayfly_run(TICKER_USER_ID_ULL_LOW);
    #endif

    extern const struct device __device_dts_ord_0;
    extern void hci_driver_poll_rx(const struct device *dev);
    hci_driver_poll_rx(&__device_dts_ord_0);
}

// Strong override: process Zephyr work queues and reschedule timer.
// The on-core controller delivers HCI events via ISR -> work queue -> sched_node.
// This function processes those queued events.
// Must guard against post-deinit execution since the scheduler node may
// still be enqueued after poll_cleanup() stops the soft timer.
static uint32_t run_task_count;
void mp_bluetooth_zephyr_port_run_task(mp_sched_node_t *node) {
    (void)node;
    if (!mp_bluetooth_is_active()) {
        return;
    }
    run_task_count++;
    mp_bluetooth_zephyr_controller_poll_rx();
    mp_bluetooth_zephyr_poll();
    mp_bluetooth_zephyr_port_poll_in_ms(10);
}

// Called by k_sem_take() to process HCI while waiting for a semaphore.
// Prevents deadlock when the main task is blocked waiting for HCI responses.
// During init (mp_bluetooth_is_active() == false), only runs mp_bluetooth_zephyr_poll()
// because the HCI driver isn't fully set up yet. After init, also polls the
// controller's rx path to deliver async events (connections, data, etc).
void mp_bluetooth_zephyr_hci_uart_wfi(void) {
    #if MICROPY_BLUETOOTH_ZEPHYR_CONTROLLER
    // Always process mayflies — needed even during init for controller setup.
    // This is safe to call without re-entrancy guard because mayfly_run()
    // is re-entrant (checks mfp[] pending flag atomically).
    mayfly_run(TICKER_USER_ID_ULL_HIGH);
    mayfly_run(TICKER_USER_ID_ULL_LOW);
    #endif

    // Poll controller rx when BLE is active (skip during init).
    // Re-entrancy guard prevents recursion via:
    //   controller_poll_rx → node_rx_recv → bt_buf_get_evt(K_FOREVER)
    //   → k_sem_take → hci_uart_wfi → controller_poll_rx (blocked)
    static bool in_poll_rx = false;
    if (mp_bluetooth_is_active() && !in_poll_rx) {
        in_poll_rx = true;
        mp_bluetooth_zephyr_controller_poll_rx();
        in_poll_rx = false;
    }
    mp_bluetooth_zephyr_poll();
}

// Main polling entry point (called by mpbthciport.c / soft timer).
void mp_bluetooth_hci_poll(void) {
    if (mp_bluetooth_is_active()) {
        mp_bluetooth_zephyr_port_run_task(NULL);
    }
}

// Non-inline version of mp_bluetooth_hci_poll_now for extmod code.
// modbluetooth_zephyr.c uses extern declaration, so needs a linkable symbol.
// Schedules immediate BLE processing via the shared sched_node.
void mp_bluetooth_hci_poll_now(void) {
    mp_bluetooth_zephyr_port_poll_now();
}

// Port init — called early during mp_bluetooth_init().
void mp_bluetooth_zephyr_port_init(void) {
    DEBUG_printf("port_init\n");

    // Force linker to keep __device_dts_ord_0
    extern const struct device __device_dts_ord_0;
    volatile const void *keep_device = &__device_dts_ord_0;
    (void)keep_device;

    // Initialise shared soft timer for periodic HCI polling.
    mp_bluetooth_zephyr_poll_init_timer();
}

// Port deinit — called during mp_bluetooth_deinit().
void mp_bluetooth_zephyr_port_deinit(void) {
    DEBUG_printf("port_deinit\n");

    // Clean up shared soft timer and sched_node.
    mp_bluetooth_zephyr_poll_cleanup();

    // Reset GATT memory pool for next init cycle.
    #if MICROPY_BLUETOOTH_ZEPHYR_GATT_POOL
    mp_bluetooth_zephyr_gatt_pool_reset();
    #endif
}

// HCI UART process stub — not needed for on-core controller.
// The controller handles HCI internally, no UART transport.
void mp_bluetooth_zephyr_hci_uart_process(void) {
    // No-op for on-core controller.
}

#endif // MICROPY_PY_BLUETOOTH && MICROPY_BLUETOOTH_ZEPHYR
