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

#ifndef MICROPY_INCLUDED_TESTS_MCUBOOT_HOST_SPI_NOR_PY_MPHAL_H
#define MICROPY_INCLUDED_TESTS_MCUBOOT_HOST_SPI_NOR_PY_MPHAL_H

// The part of py/mphal.h that drivers/memory/spiflash.c and drivers/bus/{spi,qspi}.h use. The
// QSPI protocol the model implements needs no pins; the pin functions only have to compile.

#include <stddef.h>
#include <stdint.h>

typedef uint32_t mp_uint_t;
typedef uint32_t mp_hal_pin_obj_t;

static inline void mp_hal_pin_write(mp_hal_pin_obj_t pin, int v) {
    (void)pin;
    (void)v;
}

static inline void mp_hal_pin_output(mp_hal_pin_obj_t pin) {
    (void)pin;
}

static inline void mp_hal_delay_ms(mp_uint_t ms) {
    (void)ms;
}

#endif // MICROPY_INCLUDED_TESTS_MCUBOOT_HOST_SPI_NOR_PY_MPHAL_H
