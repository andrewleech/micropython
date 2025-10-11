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

// Include MicroPython HAL to get MP_HAL_MAC_BDADDR
#include "py/mphal.h"

// Force-define CYW43 BT UART transport BEFORE any CYW43 includes
#ifdef CYW43_ENABLE_BLUETOOTH_OVER_UART
#undef CYW43_ENABLE_BLUETOOTH_OVER_UART
#endif
#define CYW43_ENABLE_BLUETOOTH_OVER_UART 1

// Define BT-specific config that should come from cyw43_configport.h
// but for some reason isn't being picked up
#define CYW43_BT_FIRMWARE_INCLUDE_FILE      "firmware/cyw43_btfw_43439.h"
#define CYW43_PIN_BT_REG_ON                 (0)
#define CYW43_PIN_BT_CTS                    (2)
#define CYW43_PIN_BT_HOST_WAKE              (3)
#define CYW43_PIN_BT_DEV_WAKE               (4)
#define CYW43_HAL_UART_READCHAR_BLOCKING_WAIT CYW43_EVENT_POLL_HOOK

// Also need to ensure CYW43_HAL_MAC_BDADDR is defined
// This should come from cyw43_config_common.h but define it here just in case
#ifndef CYW43_HAL_MAC_BDADDR
#define CYW43_HAL_MAC_BDADDR                MP_HAL_MAC_BDADDR
#endif

// Include the actual implementation
#include "../../lib/cyw43-driver/src/cyw43_bthci_uart.c"
