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

#ifndef MICROPY_INCLUDED_SHARED_MCUBOOT_MCUBOOT_SPIFLASH_H
#define MICROPY_INCLUDED_SHARED_MCUBOOT_MCUBOOT_SPIFLASH_H

#include <stdint.h>

#include "drivers/memory/spiflash.h"

// A SPI flash behind drivers/memory/spiflash.c as a device of the flash map. A port forwards
// mcuboot_port_flash_read(), _write() and _erase() of that device to these functions with the
// mp_spiflash_t it has configured (mp_spiflash_init() done). Offsets are device relative. Writes
// go to the device without a cache; erase lengths are a multiple of MP_SPIFLASH_ERASE_BLOCK_SIZE.
// An erase works block by block from the top and programs the last 16 bytes of a block to zero
// before erasing it (see flash_spiflash.c). All return 0 or a negative errno.

int mcuboot_spiflash_read(mp_spiflash_t *spif, uint32_t off, void *dst, uint32_t len);
int mcuboot_spiflash_write(mp_spiflash_t *spif, uint32_t off, const void *src, uint32_t len);
int mcuboot_spiflash_erase(mp_spiflash_t *spif, uint32_t off, uint32_t len);

#endif // MICROPY_INCLUDED_SHARED_MCUBOOT_MCUBOOT_SPIFLASH_H
