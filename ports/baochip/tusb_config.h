/*
 * This file is part of the MicroPython project, http://micropython.org/
 *
 * The MIT License (MIT)
 *
 * Copyright (c) 2026 MicroPython contributors
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

#ifndef MICROPY_INCLUDED_BAOCHIP_TUSB_CONFIG_H
#define MICROPY_INCLUDED_BAOCHIP_TUSB_CONFIG_H

// Corigine UDC IP on Baochip-1x SoC; TUP_DCD_ENDPOINT_MAX and
// TUP_RHPORT_HIGHSPEED are set by tusb_mcu.h for this MCU.
#define CFG_TUSB_MCU                OPT_MCU_CORIGINE_BAO1X
#define CFG_TUSB_OS                 OPT_OS_NONE

// SE0 is driven by PC13, which doubles as the PROG strap on Dabao.
// Driving it low forces D+/D- to ground (clean disconnect) and holds
// the PROG pin asserted during a chip reset into boot1.
#define TUD_CORIGINE_SE0_ASSERT() \
    do { gpio_set_dir(GPIO_PORT_C, 13, true); gpio_put(GPIO_PORT_C, 13, false); } while (0)
#define TUD_CORIGINE_SE0_DEASSERT() \
    do { gpio_put(GPIO_PORT_C, 13, true); gpio_set_dir(GPIO_PORT_C, 13, false); } while (0)

// UDC DMA buffers must be IFRAM-resident on Baochip-1x.
#define TUD_CORIGINE_DMA_ATTR  __attribute__((section(".dma_buffers"), aligned(64)))

// The Corigine PHY is HS-capable.  DEVCONFIG.MAX_SPEED in
// dcd_corigine_udc.c must match.
#define CFG_TUD_ENABLED             1
#define CFG_TUD_MAX_SPEED           OPT_MODE_HIGH_SPEED
#define CFG_TUH_ENABLED             0

// Control EP0 size stays at 64 bytes per spec.
#ifndef CFG_TUD_ENDPOINT0_SIZE
#define CFG_TUD_ENDPOINT0_SIZE      64
#endif

// Bulk EP buffer size: keep at HS maximum.  The shared MicroPython
// tusb_config.h sets RX/TX buffer sizes based on CFG_TUD_MAX_SPEED.
#define CFG_TUD_CDC_EP_BUFSIZE      512

// MSC and other classes are deferred.  MICROPY_HW_USB_MSC /
// MICROPY_HW_USB_CDC drive the shared config below; CDC defaults on
// for this port (set in mpconfigport.h).

// Bring in MicroPython-shared defaults (string descriptors, class
// gating, endpoint numbering).  Must come after the per-port
// definitions above so this file can override them when needed.
#include "shared/tinyusb/tusb_config.h"

#endif // MICROPY_INCLUDED_BAOCHIP_TUSB_CONFIG_H
