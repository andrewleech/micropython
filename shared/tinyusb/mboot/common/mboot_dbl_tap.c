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

#include "mboot_board.h"
#include "mboot_dbl_tap.h"

#ifdef MBOOT_DBL_TAP_REG

// Adafruit/UF2-style double-tap: every normal single-reset boot pays an
// unconditional MBOOT_DBL_TAP_DELAY_MS busy-wait so a second reset within
// that window can be observed.  The boot-delay cost is the standard trade-off
// for this entry mechanism and is intentional; boards that cannot afford it
// should leave MBOOT_DBL_TAP_REG undefined and use a different entry path.
bool mboot_dbl_tap_check(void) {
    if (MBOOT_DBL_TAP_REG == MBOOT_DBL_TAP_MAGIC) {
        // Second tap within the window: enter DFU.
        MBOOT_DBL_TAP_REG = 0;
        return true;
    }
    // First tap: arm the flag and wait for a second reset.
    MBOOT_DBL_TAP_REG = MBOOT_DBL_TAP_MAGIC;
    mboot_port_delay_ms(MBOOT_DBL_TAP_DELAY_MS);
    MBOOT_DBL_TAP_REG = 0;
    return false;
}

#else

bool mboot_dbl_tap_check(void) {
    return false;
}

#endif
