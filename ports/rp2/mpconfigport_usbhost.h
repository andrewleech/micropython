/*
 * This file is part of the MicroPython project, http://micropython.org/
 *
 * The MIT License (MIT)
 *
 * Copyright (c) 2025 Andrew Leech
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

#ifndef MICROPY_INCLUDED_RP2_MPCONFIGPORT_USBHOST_H
#define MICROPY_INCLUDED_RP2_MPCONFIGPORT_USBHOST_H

// USB Host support configuration for RP2 port

#ifndef MICROPY_HW_USB_HOST
#define MICROPY_HW_USB_HOST (1) // Support machine.USBHost
#endif

#if MICROPY_HW_USB_HOST
// Enable USB Host classes
#define MICROPY_HW_USB_HOST_CDC (1)
#define MICROPY_HW_USB_HOST_MSC (1)
#define MICROPY_HW_USB_HOST_HID (1)

// Include the TinyUSB host configuration
#include "tinyusb_port/tusb_config_host.h"

// Additional USB Host specific configuration
#define MICROPY_HW_USB_HOST_TASK_STACK_SIZE (4096)
#endif

#endif // MICROPY_INCLUDED_RP2_MPCONFIGPORT_USBHOST_H
