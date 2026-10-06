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

#ifndef MICROPY_INCLUDED_SHARED_MCUBOOT_MCUBOOT_SHADOW_H
#define MICROPY_INCLUDED_SHARED_MCUBOOT_MCUBOOT_SHADOW_H

#include <stdint.h>

// Internal interface between flash_map_backend.c and shadow.c. Not for other users; the
// public surface is in flash_map_backend/flash_map_backend.h.

// Device access with bounds checks and the fault injection hook but without any ECC policy.
// Provided by flash_map_backend.c.
int mcuboot_flash_raw_read(uint8_t dev, uint32_t off, void *dst, uint32_t len);
int mcuboot_flash_raw_write(uint8_t dev, uint32_t off, const void *src, uint32_t len);
int mcuboot_flash_raw_erase(uint8_t dev, uint32_t off, uint32_t len);

#if defined(MCUBOOT_ECC_SHADOW)

// Provided by shadow.c.
//
// The trailer sectors of the primary slot, and of the secondary slot when it is on the same
// device (MCUBOOT_SECONDARY_ECC), are "protected": each 16-byte (write unit) word has a shadow
// copy at the same relative position in MCUBOOT_AREA_SHADOW. A secondary slot on a device without
// ECC (a SPI flash) has no shadow words. Reads map ECC-invalid protected words to the shadow
// value or to the erased value. Writes program the primary word, then the shadow word. Erases
// erase the primary sector, then the shadow sector. Offsets are device relative.

// Validates the shadow layout: the shadow area and the protected slots are on the device the
// shadow lives on, sizes agree. Returns 0 or -EINVAL.
int mcuboot_shadow_layout_check(void);

int mcuboot_shadow_read(uint8_t dev, uint32_t off, void *dst, uint32_t len);
int mcuboot_shadow_write(uint8_t dev, uint32_t off, const void *src, uint32_t len);
int mcuboot_shadow_erase(uint8_t dev, uint32_t off, uint32_t len);
int mcuboot_shadow_scrub(void);

#endif // MCUBOOT_ECC_SHADOW

#endif // MICROPY_INCLUDED_SHARED_MCUBOOT_MCUBOOT_SHADOW_H
