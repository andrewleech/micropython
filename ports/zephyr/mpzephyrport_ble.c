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

// Native Zephyr port hooks for extmod/zephyr_ble/modbluetooth_zephyr.c.
//
// On this port the Bluetooth host and controller run inside a real Zephyr
// kernel, using its own threads (system workqueue, BT RX/TX) and real
// k_timer/k_work/k_sem primitives. None of extmod/zephyr_ble/hal's
// cooperative-scheduling shims are built here, so the weak defaults they
// provide for other ports (soft-timer driven polling, a dedicated HCI RX
// task, etc.) do not apply: Zephyr's own scheduler delivers HCI events and
// runs work items autonomously while this thread blocks or yields.

#include "py/mpconfig.h"

#if MICROPY_PY_BLUETOOTH && MICROPY_BLUETOOTH_ZEPHYR

#include <zephyr/kernel.h>
#include "extmod/zephyr_ble/hal/zephyr_ble_port.h"
#include "extmod/zephyr_ble/hal/zephyr_ble_work.h"

// No port-specific state to set up or tear down: the Zephyr Bluetooth
// subsystem owns its own threads, timers and work queues.
void mp_bluetooth_zephyr_port_init(void) {
}

void mp_bluetooth_zephyr_port_deinit(void) {
}

// The HAL uses these two hooks to bracket bt_disable() with cooperative
// work-queue cleanup it has to do by hand (see hal/zephyr_ble_work.c). On
// this port the system workqueue runs its own thread and bt_disable()
// itself performs the equivalent teardown (bt_conn_cleanup_all(), pool and
// key resets, ...), so there is nothing left for the port to do here.
void mp_bluetooth_zephyr_port_pre_disable(void) {
}

void mp_bluetooth_zephyr_port_post_disable(void) {
}

// There is no separate HCI polling cycle to (re)arm here: Zephyr's own BT
// RX thread delivers HCI events to the registered host callbacks as they
// arrive.
void mp_bluetooth_hci_poll_now(void) {
}

// k_sem_take() blocks this thread inside Zephyr's real scheduler, which
// keeps running other threads (including the Bluetooth host's own RX/TX
// threads) while we wait. No manual pump is needed to avoid deadlock.
void mp_bluetooth_zephyr_hci_uart_wfi(void) {
}

// No pump is needed here: the system workqueue runs at
// CONFIG_SYSTEM_WORKQUEUE_PRIORITY (-1, cooperative), above this thread's
// CONFIG_MAIN_THREAD_PRIORITY (0, preemptible). Work submitted by the
// Bluetooth host therefore preempts and runs before k_work_submit() even
// returns to its caller, so by the time control reaches this function the
// work is already done. The k_yield() is a defensive no-op kept for the
// same call signature as the other ports; it does not itself wait for
// anything (k_yield() only yields to threads already ready to run).
void mp_bluetooth_zephyr_work_process(void) {
    k_yield();
}

// --- HCI RX task stubs ---
// Native Zephyr has no separate MicroPython-managed HCI RX task: Zephyr's
// own Bluetooth RX thread fills that role.
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
