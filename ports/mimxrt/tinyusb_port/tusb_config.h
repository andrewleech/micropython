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

#ifndef _MIMXRT_TUSB_CONFIG_H_
#define _MIMXRT_TUSB_CONFIG_H_

// Include MicroPython configuration to get MICROPY_HW_USB_HOST.
#include "py/mpconfig.h"

// RHPort mode - host, device, or dual-port based on config.
#if MICROPY_HW_USB_HOST && MICROPY_HW_ENABLE_USBDEV
// Dual-port mode: RHPORT0 = device, RHPORT1 = host.
#include "tusb_config_dual.h"
#elif MICROPY_HW_USB_HOST
// Host-only mode: RHPORT0 = host.
#define CFG_TUSB_RHPORT0_MODE   (OPT_MODE_HOST | OPT_MODE_HIGH_SPEED)
#include "tusb_config_host.h"
#else
// Device-only mode: RHPORT0 = device.
#define CFG_TUSB_RHPORT0_MODE   (OPT_MODE_DEVICE | OPT_MODE_HIGH_SPEED)
#endif

// Include shared TinyUSB config for device-mode definitions.
#include "shared/tinyusb/tusb_config.h"

#endif // _MIMXRT_TUSB_CONFIG_H_
