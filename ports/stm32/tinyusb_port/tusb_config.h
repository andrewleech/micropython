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
#ifndef MICROPY_INCLUDED_STM32_TINYUSB_PORT_TUSB_CONFIG_H
#define MICROPY_INCLUDED_STM32_TINYUSB_PORT_TUSB_CONFIG_H

#include "py/mpconfig.h"

#include "tinyusb_port/tusb_rhport.h"

#if defined(USB_HS_PHYC) && !defined(USB_HS_PHYC_PLL1_PLLSEL_Pos)
// Providing missing definitions of USB_HS_PHYC_PLL1_PLLSEL constants, required by TinyUSB.
// (These are added in a newer version of the STM32F7xx CMSIS files.)
#define USB_HS_PHYC_PLL1_PLLSEL_Pos               (1U)
#define USB_HS_PHYC_PLL1_PLLSEL_Msk               (0x7UL << USB_HS_PHYC_PLL1_PLLSEL_Pos)
#define USB_HS_PHYC_PLL1_PLLSEL                   (USB_HS_PHYC_PLL1_PLLSEL_Msk)
#define USB_HS_PHYC_PLL1_PLLSEL_1                 (0x1UL << USB_HS_PHYC_PLL1_PLLSEL_Pos)
#define USB_HS_PHYC_PLL1_PLLSEL_2                 (0x2UL << USB_HS_PHYC_PLL1_PLLSEL_Pos)
#define USB_HS_PHYC_PLL1_PLLSEL_3                 (0x4UL << USB_HS_PHYC_PLL1_PLLSEL_Pos)
#define USB_HS_PHYC_PLL1_PLLSEL_12MHZ             (0x00000000U)
#define USB_HS_PHYC_PLL1_PLLSEL_12_5MHZ           (USB_HS_PHYC_PLL1_PLLSEL_1)
#define USB_HS_PHYC_PLL1_PLLSEL_16MHZ             (USB_HS_PHYC_PLL1_PLLSEL_1 | USB_HS_PHYC_PLL1_PLLSEL_2)
#define USB_HS_PHYC_PLL1_PLLSEL_24MHZ             (USB_HS_PHYC_PLL1_PLLSEL_3)
#define USB_HS_PHYC_PLL1_PLLSEL_25MHZ             (USB_HS_PHYC_PLL1_PLLSEL_2 | USB_HS_PHYC_PLL1_PLLSEL_3)
#endif

#include "shared/tinyusb/tusb_config.h"

#endif // MICROPY_INCLUDED_STM32_TINYUSB_PORT_TUSB_CONFIG_H
