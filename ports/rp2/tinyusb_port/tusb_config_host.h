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

#ifndef _RP2_TUSB_CONFIG_HOST_H_
#define _RP2_TUSB_CONFIG_HOST_H_

#if MICROPY_HW_USB_HOST

// Host mode configuration
#define CFG_TUH_ENABLED          1
#define CFG_TUH_DEVICE_MAX       4
#define CFG_TUH_ENDPOINT_MAX     16

// Enable host classes
#define CFG_TUH_CDC              4
#define CFG_TUH_MSC              2
#define CFG_TUH_HID              4

// Memory allocation for host mode
#define CFG_TUH_MEM_SECTION
#define CFG_TUH_MEM_ALIGN        __attribute__((aligned(4)))

// Host specific settings
#define BOARD_TUH_RHPORT         0
#define CFG_TUH_HUB              1
#define CFG_TUH_ENUMERATION_BUFSIZE  256

#endif // MICROPY_HW_USB_HOST

#endif // _RP2_TUSB_CONFIG_HOST_H_
