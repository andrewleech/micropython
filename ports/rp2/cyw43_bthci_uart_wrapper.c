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

// Wrapper to include cyw43_bthci_uart.c with proper CYW43 BT configuration
//
// NOTE: The BT firmware data is defined in cybt_shared_bus.c (part of
// cyw43_driver_picow library). To avoid duplicate definitions, we declare
// the firmware data as extern here and skip including the firmware header.

// Include MicroPython HAL
#include "py/mphal.h"

// Force-define CYW43 BT UART transport before any CYW43 includes
#ifdef CYW43_ENABLE_BLUETOOTH_OVER_UART
#undef CYW43_ENABLE_BLUETOOTH_OVER_UART
#endif
#define CYW43_ENABLE_BLUETOOTH_OVER_UART 1

// Use stub firmware header that declares data as extern
#define CYW43_BT_FIRMWARE_INCLUDE_FILE "cyw43_btfw_43439_extern.h"

// Define MAC address index for BDADDR
#ifndef CYW43_HAL_MAC_BDADDR
#define CYW43_HAL_MAC_BDADDR MP_HAL_MAC_BDADDR
#endif

// Define other BT config that should come from cyw43_configport.h
#ifndef CYW43_PIN_BT_REG_ON
#define CYW43_PIN_BT_REG_ON                 (0)
#endif
#ifndef CYW43_PIN_BT_CTS
#define CYW43_PIN_BT_CTS                    (2)
#endif
#ifndef CYW43_PIN_BT_HOST_WAKE
#define CYW43_PIN_BT_HOST_WAKE              (3)
#endif
#ifndef CYW43_PIN_BT_DEV_WAKE
#define CYW43_PIN_BT_DEV_WAKE               (4)
#endif
#ifndef CYW43_HAL_UART_READCHAR_BLOCKING_WAIT
#define CYW43_HAL_UART_READCHAR_BLOCKING_WAIT mp_event_handle_nowait()
#endif

// Include the actual implementation
#include "../../lib/cyw43-driver/src/cyw43_bthci_uart.c"
