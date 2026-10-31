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

#ifndef MICROPY_INCLUDED_MIMXRT_RESET_CAUSE_H
#define MICROPY_INCLUDED_MIMXRT_RESET_CAUSE_H

#include <stdint.h>
typedef enum {
    MIMXRT_RESET_CAUSE_OTHER,
    MIMXRT_RESET_CAUSE_POR,
    MIMXRT_RESET_CAUSE_WATCHDOG,
    MIMXRT_RESET_CAUSE_PIN,
    MIMXRT_RESET_CAUSE_SOFTWARE,
    MIMXRT_RESET_CAUSE_OVERVOLTAGE,
} mimxrt_reset_cause_t;

mimxrt_reset_cause_t mimxrt_reset_cause_classify(uint32_t src_flags, uint32_t wdog_flags);

#endif
