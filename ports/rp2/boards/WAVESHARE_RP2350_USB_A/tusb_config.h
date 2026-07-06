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

// Dual-mode USB: native device (USB-C) + PIO USB host (USB-A)
// These are defined in mpconfigboard.h for early visibility but
// repeated here with guards for TinyUSB source files that include
// tusb_config.h without going through mpconfigboard.h first.
#ifndef BOARD_TUD_RHPORT
#define BOARD_TUD_RHPORT     0
#endif
#ifndef BOARD_TUH_RHPORT
#define BOARD_TUH_RHPORT     1
#endif
#ifndef CFG_TUH_RPI_PIO_USB
#define CFG_TUH_RPI_PIO_USB  1
#endif

#endif // MICROPY_INCLUDED_RP2_TUSB_CONFIG_H
