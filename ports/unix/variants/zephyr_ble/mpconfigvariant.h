/*
 * This file is part of the MicroPython project, http://micropython.org/
 *
 * The MIT License (MIT)
 *
 * Copyright (c) 2026 Andrew Leech
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

// Unix port variant with Zephyr BLE stack using HCI_CHANNEL_USER sockets.

// Set base feature level.
#define MICROPY_CONFIG_ROM_LEVEL (MICROPY_CONFIG_ROM_LEVEL_EXTRA_FEATURES)

// Enable extra Unix features.
#include "../mpconfigvariant_common.h"

// Soft timer support (pthread-based backend).
#define MICROPY_PY_MACHINE_TIMER (1)
#define MICROPY_SCHEDULER_STATIC_NODES (1)

// PendSV-equivalent mutex for soft timer thread safety.
// soft_timer_handler() runs in the timer thread; PENDSV_ENTER/EXIT
// protect the timer heap from concurrent access by the main thread.
// Must be recursive: soft timer C callbacks may call soft_timer_insert().
extern void mp_unix_pendsv_enter(void);
extern void mp_unix_pendsv_exit(void);
#define MICROPY_PY_PENDSV_ENTER mp_unix_pendsv_enter();
#define MICROPY_PY_PENDSV_EXIT mp_unix_pendsv_exit();

// BLE critical sections use a recursive pthread mutex to protect shared
// state between the main thread and the dedicated HCI RX thread.
// The atomic_state variable is kept for API compatibility with call sites
// that pass it to helper functions (e.g. schedule_ringbuf in modbluetooth.c).
extern void mp_bluetooth_zephyr_ble_lock(void);
extern void mp_bluetooth_zephyr_ble_unlock(void);
#define MICROPY_PY_BLUETOOTH_ENTER mp_bluetooth_zephyr_ble_lock(); uint32_t atomic_state = 0; (void)atomic_state;
#define MICROPY_PY_BLUETOOTH_EXIT (void)atomic_state; mp_bluetooth_zephyr_ble_unlock();

// Register machine.Timer.
extern const struct _mp_obj_type_t machine_timer_type;
#define MICROPY_PY_MACHINE_EXTRA_GLOBALS \
    { MP_ROM_QSTR(MP_QSTR_Timer), MP_ROM_PTR(&machine_timer_type) },

// MICROPY_SCHED_HOOK_SCHEDULED is defined in mpconfigport.h via PR #18810
// (mp_hal_signal_event) which sends a signal to wake select() via EINTR.

// Process BLE work queue and pending scheduler callbacks during event waits.
// The HCI RX thread delivers raw HCI packets; work_process on the main thread
// processes them into Zephyr host events (connection maintenance, GATT, etc).
// Without this, connections drop due to supervision timeout because the host
// doesn't process LL keepalives fast enough.
extern _Bool mp_bluetooth_zephyr_poll(void);
#define MICROPY_INTERNAL_EVENT_HOOK \
    do { mp_bluetooth_zephyr_poll(); mp_handle_pending(true); } while (0);

// machine.idle() must process BLE events.  The multitest wait_for_event()
// loops call machine.idle() which is a no-op by default on unix.
#include <sched.h>
// Process BLE work, yield to RX thread, then process pending callbacks.
// The sched_yield() between poll and handle_pending is critical: without
// it the main thread starves the RX thread by holding the BLE mutex in
// work_process continuously, preventing HCI event delivery.
#define MICROPY_UNIX_MACHINE_IDLE \
    do { mp_bluetooth_zephyr_poll(); sched_yield(); mp_handle_pending(true); } while (0);

// Zephyr BLE -- most BLE defines are provided by zephyr_ble.mk via CFLAGS_EXTMOD.
// Only define what the makefile does not provide.
#define MICROPY_PY_BLUETOOTH_ENABLE_CENTRAL_MODE (1)
#define MICROPY_BLUETOOTH_ZEPHYR_GATT_POOL (0) // Unix has libc malloc
