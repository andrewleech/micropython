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
#include "py/mperrno.h"
#include "py/mphal.h"

#if MICROPY_PY_BLUETOOTH && MICROPY_BLUETOOTH_ZEPHYR

#define DEBUG_printf(...) mp_printf(&mp_plat_print, "mpzephyrport.c: " __VA_ARGS__)

#include "extmod/modbluetooth.h"
#include "extmod/mpbthci.h"
#include "mpbthciport.h"

// Get any pending data from the HCI UART and send it to Zephyr's HCI buffers.
// Process Zephyr work queues and semaphores.
void mp_bluetooth_hci_poll(void) {
    // Only poll if BLE is active
    if (mp_bluetooth_is_active()) {
        DEBUG_printf("mp_bluetooth_hci_poll\n");

        // Process Zephyr BLE work queues and semaphores
        // This handles all pending work items, timers, and events
        extern void mp_bluetooth_zephyr_poll(void);
        mp_bluetooth_zephyr_poll();

        // Call this function again in 128ms to check for new events
        // TODO: improve this by only calling back when needed
        mp_bluetooth_hci_poll_in_ms(128);
    }
}

// Wait-for-interrupt during Zephyr semaphore waits
// This is called from k_sem_take busy-wait loops
void mp_bluetooth_zephyr_hci_uart_wfi(void) {
    #if defined(__WFE)
    // Use WFE (Wait For Event) for efficient sleeping on ARM
    // Timeout after 1ms to check for HCI data and other events
    extern void best_effort_wfe_or_timeout(absolute_time_t t);
    extern absolute_time_t make_timeout_time_ms(uint32_t ms);
    best_effort_wfe_or_timeout(make_timeout_time_ms(1));
    #else
    // Fallback for non-ARM: just delay
    mp_hal_delay_us(1000);
    #endif

    // Process any pending HCI UART data during wait
    // This ensures HCI responses don't get delayed
    // Note: This must not invoke Python code or raise exceptions
    extern void mp_bluetooth_zephyr_poll_uart(void);
    mp_bluetooth_zephyr_poll_uart();
}

#endif // MICROPY_PY_BLUETOOTH && MICROPY_BLUETOOTH_ZEPHYR
