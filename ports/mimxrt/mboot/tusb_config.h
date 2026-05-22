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

#ifndef MICROPY_INCLUDED_MIMXRT_MBOOT_TUSB_CONFIG_H
#define MICROPY_INCLUDED_MIMXRT_MBOOT_TUSB_CONFIG_H

// TinyUSB configuration for mimxrt mboot.
//
// CFG_TUSB_MCU is set to OPT_MCU_MIMXRT1XXX (== OPT_MCU_MIMXRT10XX ==
// OPT_MCU_MIMXRT11XX in this version of TinyUSB; all three alias to 700).
// The board-supplied define MBOOT_MCU_MIMXRT11XX or MBOOT_MCU_MIMXRT10XX
// distinguishes run-time USB controller selection in mboot_port.c but does
// not change the TinyUSB MCU identifier.
#ifndef CFG_TUSB_MCU
#define CFG_TUSB_MCU OPT_MCU_MIMXRT1XXX
#endif

// Enable the device stack on rhport 0.
// Without this TinyUSB does not compile in the device stack and tusb_init()
// becomes a no-op macro.
#if defined(MBOOT_MCU_MIMXRT11XX)
#define CFG_TUSB_RHPORT0_MODE (OPT_MODE_DEVICE | OPT_MODE_HIGH_SPEED)
#else
#define CFG_TUSB_RHPORT0_MODE (OPT_MODE_DEVICE | OPT_MODE_FULL_SPEED)
#endif

// Bare-metal, no OS.
#define CFG_TUSB_OS OPT_OS_NONE

// No RTOS task; tud_task() is polled from the main loop.
#define CFG_TUSB_DEBUG 0

// Disable all class drivers except DFU mode.
// We use TinyUSB's built-in DFU device class driver (dfu_device.c).
// CFG_TUD_DFU enables the DFU mode class driver.
#define CFG_TUD_DFU 1

// Transfer size advertised in the DFU functional descriptor.
// Must match MBOOT_USBD_XFER_SIZE in mboot_usbd.h and
// MBOOT_DFU_XFER_SIZE in mboot_dfu.h.
#define CFG_TUD_DFU_XFER_BUFSIZE 2048

// Disable all other device classes.
#define CFG_TUD_CDC 0
#define CFG_TUD_MSC 0
#define CFG_TUD_HID 0
#define CFG_TUD_MIDI 0
// CFG_TUD_VENDOR=1 enables the vendor request dispatch path required for
// mboot's out-of-band erase opcode (MBOOT_VREQ_ERASE, bRequest=0x80).
// Without it, TinyUSB silently drops vendor-typed control transfers.
#define CFG_TUD_VENDOR 1

// CFG_TUD_MAX_SPEED is derived from CFG_TUSB_RHPORT0_MODE above by TinyUSB
// (see tusb_option.h:348-351).  No explicit definition needed.

// On Cortex-M7 RT10xx/RT11xx, DTCM/ITCM are only accessible via the core's
// TCM interface and cannot be reached by AHB bus masters (USB DMA, EDMA, etc.).
// Place all TinyUSB DMA-accessible buffers (_dcd_data, _ctrl_epbuf, _dfu_epbuf)
// in OCRAM (0x20200000) which is on the AHB crossbar and reachable by USB DMA.
#define CFG_TUSB_MEM_SECTION  __attribute__((section(".usb_dma_noninit")))

#endif // MICROPY_INCLUDED_MIMXRT_MBOOT_TUSB_CONFIG_H
