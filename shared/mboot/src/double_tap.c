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

// Double-tap entry keeps its marker separate from the request and retention words, so
// consuming a request cannot cancel a tap. The check blocks for the whole detection window
// unless it finds the marker from a reset inside the preceding window.

#include "mboot_layout.h"

#if MICROPY_MBOOT_DOUBLE_TAP

#include "mboot_port.h"

bool mboot_double_tap_check(void) {
    volatile uint32_t *word = mboot_port_double_tap_word();
    if (*word == MICROPY_MBOOT_DOUBLE_TAP_MAGIC) {
        *word = 0;
        return true;
    }
    *word = MICROPY_MBOOT_DOUBLE_TAP_MAGIC;
    mboot_port_delay_ms(MICROPY_MBOOT_DOUBLE_TAP_MS);
    *word = 0;
    return false;
}

#endif
