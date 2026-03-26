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

// Unix port variant with Zephyr BLE stack using HCI_CHANNEL_USER sockets.

// Set base feature level.
#define MICROPY_CONFIG_ROM_LEVEL (MICROPY_CONFIG_ROM_LEVEL_EXTRA_FEATURES)

// Enable extra Unix features.
#include "../mpconfigvariant_common.h"

// Soft timer (pthread-based)
#define MICROPY_PY_MACHINE_TIMER (1)
#define MICROPY_SCHEDULER_STATIC_NODES (1)

// Register machine.Timer in the machine module.
#define MICROPY_PY_MACHINE_EXTRA_GLOBALS \
    { MP_ROM_QSTR(MP_QSTR_Timer), MP_ROM_PTR(&machine_timer_type) },

// PendSV-equivalent recursive mutex for soft timer thread safety.
extern void mp_unix_pendsv_enter(void);
extern void mp_unix_pendsv_exit(void);
#define MICROPY_PY_PENDSV_ENTER mp_unix_pendsv_enter();
#define MICROPY_PY_PENDSV_EXIT mp_unix_pendsv_exit();

// BLE runs in the scheduler on Unix (cooperative, single-threaded), so
// critical sections are no-ops. Defined here because zephyr_ble_atomic.h
// needs them at py/mpconfig.h include time, before modbluetooth.h.
#define MICROPY_PY_BLUETOOTH_ENTER uint32_t atomic_state = 0;
#define MICROPY_PY_BLUETOOTH_EXIT (void)atomic_state;

// Zephyr BLE -- most BLE defines are provided by zephyr_ble.mk via CFLAGS_EXTMOD.
// Only define what the makefile does not provide.
#define MICROPY_PY_BLUETOOTH_ENABLE_CENTRAL_MODE (1)
#define MICROPY_BLUETOOTH_ZEPHYR_GATT_POOL (0) // Unix has libc malloc
