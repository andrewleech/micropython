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

#include "mimxrt_usb_phy.h"

#include "fsl_device_registers.h"
#include CPU_HEADER_H

void mimxrt_usb_phy_init(uint8_t d_cal, uint8_t txcal45dp, uint8_t txcal45dn) {
    #ifdef USBPHY1
    USBPHY_Type *usb_phy = USBPHY1;
    #else
    USBPHY_Type *usb_phy = USBPHY;
    #endif

    #if defined(MIMXRT117x_SERIES)
    usb_phy->TRIM_OVERRIDE_EN = USBPHY_TRIM_OVERRIDE_EN_TRIM_DIV_SEL_OVERRIDE(1) |
        USBPHY_TRIM_OVERRIDE_EN_TRIM_ENV_TAIL_ADJ_VD_OVERRIDE(1) |
        USBPHY_TRIM_OVERRIDE_EN_TRIM_TX_D_CAL_OVERRIDE(1) |
        USBPHY_TRIM_OVERRIDE_EN_TRIM_TX_CAL45DP_OVERRIDE(1) |
        USBPHY_TRIM_OVERRIDE_EN_TRIM_TX_CAL45DN_OVERRIDE(1);
    #endif
    usb_phy->PWD = 0U;
    usb_phy->TX = (usb_phy->TX & ~(USBPHY_TX_D_CAL_MASK | USBPHY_TX_TXCAL45DM_MASK | USBPHY_TX_TXCAL45DP_MASK)) |
        USBPHY_TX_D_CAL(d_cal) | USBPHY_TX_TXCAL45DP(txcal45dp) | USBPHY_TX_TXCAL45DM(txcal45dn);
}
