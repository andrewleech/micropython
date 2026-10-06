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
#ifndef MICROPY_INCLUDED_STM32_MCUBOOT_TUSB_CONFIG_H
#define MICROPY_INCLUDED_STM32_MCUBOOT_TUSB_CONFIG_H

// TinyUSB configuration of the STM32 MCUboot bootloader: a full-speed device with the DFU class
// only, polled from the main loop. On STM32H5 it is the USB DRD FS controller (rhport 0). On
// STM32F7 it is the OTG_HS core (rhport 1, rhport 0 is not used) in full-speed mode on its
// internal PHY.

// The series is told by its CMSIS header: its device macro (STM32H5, STM32F7) is only defined by
// including it.
#if __has_include("stm32h5xx.h")
#define CFG_TUSB_MCU OPT_MCU_STM32H5
#define CFG_TUSB_RHPORT0_MODE (OPT_MODE_DEVICE | OPT_MODE_FULL_SPEED)
#else
#define CFG_TUSB_MCU OPT_MCU_STM32F7
#define CFG_TUSB_RHPORT0_MODE (OPT_MODE_NONE)
#define CFG_TUSB_RHPORT1_MODE (OPT_MODE_DEVICE | OPT_MODE_FULL_SPEED)
#endif
#define CFG_TUSB_OS OPT_OS_NONE
#define CFG_TUSB_DEBUG 0

// The DFU core (shared/tinyusb/mboot) supplies the descriptors and the DFU callbacks.
// CFG_TUD_DFU_XFER_BUFSIZE must equal MBOOT_USBD_XFER_SIZE.
#define CFG_TUD_DFU 1
#define CFG_TUD_DFU_XFER_BUFSIZE 2048

// The DFU core's out-of-band erase and result requests are vendor control requests. usbd.c
// hands them to tud_vendor_control_xfer_cb() without a class driver, so the vendor class is not
// needed and the configuration has the DFU interface only.
#define CFG_TUD_VENDOR 0

#define CFG_TUD_CDC 0
#define CFG_TUD_MSC 0
#define CFG_TUD_HID 0
#define CFG_TUD_MIDI 0

#endif // MICROPY_INCLUDED_STM32_MCUBOOT_TUSB_CONFIG_H
