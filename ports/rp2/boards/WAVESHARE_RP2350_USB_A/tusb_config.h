/*
 * The MIT License (MIT)
 *
 * Copyright (c) 2024 Waveshare
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

#ifndef MICROPY_INCLUDED_RP2_TUSB_CONFIG_H
#define MICROPY_INCLUDED_RP2_TUSB_CONFIG_H

#include "py/mpconfig.h"
#include "mpconfigport.h"

// Use the default TinyUSB config
#include "../../tusb_config.h"

// Use native USB controllers for both host and device mode on RP2350
#if CFG_TUH_ENABLED
#define CFG_TUH_RPI_PIO_USB   0  // Disable PIO USB, use native controllers
#define BOARD_TUH_RHPORT      1  // Use USB1 for host mode

// Use native USB for device mode
#define BOARD_TUD_RHPORT      0  // Use USB0 for device mode
#endif

#endif // MICROPY_INCLUDED_RP2_TUSB_CONFIG_H
