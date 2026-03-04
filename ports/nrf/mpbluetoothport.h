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

#ifndef MICROPY_INCLUDED_NRF_MPBLUETOOTHPORT_H
#define MICROPY_INCLUDED_NRF_MPBLUETOOTHPORT_H

#if MICROPY_BLUETOOTH_ZEPHYR

// IRQ priorities for the Zephyr BLE controller on nRF52840.
// nRF52840 Cortex-M4 has 3 priority bits (8 levels: 0 highest, 7 lowest).
// - RADIO + TIMER0 at highest priority (real-time radio timing)
// - RTC0 ticker and LLL mayfly at next level
// - ULL (upper link layer) at lower priority
// - PendSV at lowest for deferred processing
#define ZEPHYR_BLE_IRQ_PRI_RADIO    0  // RADIO_IRQHandler, TIMER0_IRQHandler
#define ZEPHYR_BLE_IRQ_PRI_RTC0     1  // RTC0_IRQHandler (ticker)
#define ZEPHYR_BLE_IRQ_PRI_SWI_LLL  1  // SWI4 (LLL mayfly)
#define ZEPHYR_BLE_IRQ_PRI_SWI_ULL  2  // SWI5 (ULL low mayfly)
#define ZEPHYR_BLE_IRQ_PRI_PENDSV   7  // PendSV (lowest on Cortex-M4)

// Resource reservations when BLE controller is active:
// - RNG peripheral: owned by controller for entropy
// - PPI channels: used by controller for radio->timer routing
// - RTC0: reserved for controller ticker (RTC1 used by MicroPython time ticks)
// - TIMER0: reserved for radio timing
// - ECB: AES encryption engine used by controller
// - CCM/AAR: crypto/address resolution used by controller

// ENTER/EXIT macros for BLE callback context protection.
// In cooperative mode, BLE runs in scheduler context (main thread)
// so no special locking is needed.
// Variable name must be "atomic_state" to match zephyr_ble_atomic.h usage.
#define MICROPY_PY_BLUETOOTH_ENTER uint32_t atomic_state = 0;
#define MICROPY_PY_BLUETOOTH_EXIT (void)atomic_state;

#endif // MICROPY_BLUETOOTH_ZEPHYR

#endif // MICROPY_INCLUDED_NRF_MPBLUETOOTHPORT_H
