/*
 * This file is part of the MicroPython project, http://micropython.org/
 *
 * The MIT License (MIT)
 *
 * Copyright (c) 2026 Andrew Leech
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

#ifndef MICROPY_INCLUDED_PORTS_RP2_MBOOT_TUSB_CONFIG_H
#define MICROPY_INCLUDED_PORTS_RP2_MBOOT_TUSB_CONFIG_H

// tusb_config.h for the rp2 mboot DFU bootloader.
//
// This file is self-contained: it does not include py/mpconfig.h or any
// MicroPython-VM header.  CFG_TUSB_MCU is supplied by the pico-sdk
// tinyusb_device interface library (OPT_MCU_RP2040 / OPT_MCU_RP2350).

// OS abstraction: bare-metal, no RTOS.
// pico-sdk may define CFG_TUSB_OS via compile definitions; guard to avoid redefinition.
#ifndef CFG_TUSB_OS
#define CFG_TUSB_OS OPT_OS_NONE
#endif

// Device-mode only on RHPort 0.
#ifndef CFG_TUSB_RHPORT0_MODE
#define CFG_TUSB_RHPORT0_MODE OPT_MODE_DEVICE
#endif

// No debug output from TinyUSB; saves code space.
#define CFG_TUSB_DEBUG 0

// Enable the DFU class driver.  mboot_usbd.c provides all tud_dfu_*_cb
// callbacks; see shared/tinyusb/mboot/common/mboot_usbd.c.
#define CFG_TUD_DFU 1

// DFU transfer buffer size must match MBOOT_DFU_XFER_SIZE (2048).
#define CFG_TUD_DFU_XFER_BUFSIZE 2048

// Disable all other device classes — bootloader only.
#define CFG_TUD_CDC 0
#define CFG_TUD_MSC 0
#define CFG_TUD_HID 0
#define CFG_TUD_MIDI 0
// CFG_TUD_VENDOR=1 enables the vendor request dispatch path required for
// mboot's out-of-band erase opcode (MBOOT_VREQ_ERASE, bRequest=0x80).
// Without it, TinyUSB silently drops vendor-typed control transfers.
#define CFG_TUD_VENDOR 1
#define CFG_TUD_DFU_RUNTIME 0

#endif // MICROPY_INCLUDED_PORTS_RP2_MBOOT_TUSB_CONFIG_H
