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

#ifndef MICROPY_INCLUDED_SHARED_TINYUSB_TUSB_CONFIG_HOST_H
#define MICROPY_INCLUDED_SHARED_TINYUSB_TUSB_CONFIG_HOST_H

#if MICROPY_HW_USB_HOST

// Host mode configuration.
#define CFG_TUH_ENABLED          1

// Allow board-specific overrides for memory-constrained devices.
#ifndef CFG_TUH_DEVICE_MAX
#define CFG_TUH_DEVICE_MAX       4
#endif

#ifndef CFG_TUH_ENDPOINT_MAX
#define CFG_TUH_ENDPOINT_MAX     16
#endif

// Enable host classes - allow board overrides.
#ifndef CFG_TUH_CDC
#define CFG_TUH_CDC              4
#endif

#ifndef CFG_TUH_MSC
#define CFG_TUH_MSC              2
#endif

#ifndef CFG_TUH_HID
#define CFG_TUH_HID              4
#endif

// Hub support - allow board override.
#ifndef CFG_TUH_HUB
#define CFG_TUH_HUB              1
#endif

// Memory allocation for host mode.
// Boards can override CFG_TUH_MEM_SECTION to place data in specific memory regions.
#ifndef CFG_TUH_MEM_SECTION
#define CFG_TUH_MEM_SECTION
#endif

#define CFG_TUH_MEM_ALIGN        __attribute__((aligned(4)))

// Host specific settings.
#ifndef BOARD_TUH_RHPORT
#define BOARD_TUH_RHPORT         0
#endif

#define CFG_TUH_ENUMERATION_BUFSIZE  256

#endif // MICROPY_HW_USB_HOST

#endif // MICROPY_INCLUDED_SHARED_TINYUSB_TUSB_CONFIG_HOST_H
