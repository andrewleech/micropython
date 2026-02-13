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

#ifndef _MIMXRT_TUSB_CONFIG_DUAL_H_
#define _MIMXRT_TUSB_CONFIG_DUAL_H_

// Dual-port configuration: RHPORT0 = device, RHPORT1 = host.
// This allows simultaneous USB device (for REPL) and USB host operation.

// RHPORT0: USB Device mode (USB_OTG1 - for CDC REPL).
#define CFG_TUSB_RHPORT0_MODE   (OPT_MODE_DEVICE | OPT_MODE_HIGH_SPEED)

// RHPORT1: USB Host mode (USB_OTG2 - for external USB devices).
#define CFG_TUSB_RHPORT1_MODE   (OPT_MODE_HOST | OPT_MODE_HIGH_SPEED)

// Enable both stacks.
#define CFG_TUD_ENABLED          1
#define CFG_TUH_ENABLED          1

// Device configuration - use RHPORT0 for device.
#ifndef BOARD_TUD_RHPORT
#define BOARD_TUD_RHPORT         0
#endif

// Host configuration - use RHPORT1 for host.
#ifndef BOARD_TUH_RHPORT
#define BOARD_TUH_RHPORT         1
#endif

// NOTE: TinyUSB automatically determines TUD_OPT_RHPORT and TUH_OPT_RHPORT
// from CFG_TUSB_RHPORT0_MODE and CFG_TUSB_RHPORT1_MODE, so no need to define them here.

// Host mode configuration - allow board overrides.
#ifndef CFG_TUH_DEVICE_MAX
#define CFG_TUH_DEVICE_MAX       4
#endif

#ifndef CFG_TUH_ENDPOINT_MAX
#define CFG_TUH_ENDPOINT_MAX     16
#endif

#ifndef CFG_TUH_CDC
#define CFG_TUH_CDC              4
#endif

#ifndef CFG_TUH_MSC
#define CFG_TUH_MSC              2
#endif

#ifndef CFG_TUH_HID
#define CFG_TUH_HID              4
#endif

#ifndef CFG_TUH_HUB
#define CFG_TUH_HUB              1
#endif

// Memory allocation for host mode.
#ifndef CFG_TUH_MEM_SECTION
#define CFG_TUH_MEM_SECTION      __attribute__((section(".usb_host_ram")))
#endif

#define CFG_TUH_MEM_ALIGN        __attribute__((aligned(4)))
#define CFG_TUH_ENUMERATION_BUFSIZE  256
#define CFG_TUH_MAX_SPEED        OPT_MODE_HIGH_SPEED

#endif // _MIMXRT_TUSB_CONFIG_DUAL_H_
