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

#ifndef MICROPY_INCLUDED_SHARED_MBOOT_SYSFLASH_SYSFLASH_H
#define MICROPY_INCLUDED_SHARED_MBOOT_SYSFLASH_SYSFLASH_H

#include <mcuboot_config/mcuboot_config.h>

// Flash area ids. Only one image is supported. With the single slot policy both image
// slots map to the same area, as the Zephyr port does.

#define FLASH_AREA_BOOTLOADER           0
#define FLASH_AREA_IMAGE_PRIMARY(x)     1
#if defined(MCUBOOT_SINGLE_APPLICATION_SLOT)
#define FLASH_AREA_IMAGE_SECONDARY(x)   1
#else
#define FLASH_AREA_IMAGE_SECONDARY(x)   2
#endif
#define FLASH_AREA_IMAGE_SCRATCH        3

// Areas outside bootutil's view.
#define MBOOT_AREA_LOG                4
#define MBOOT_AREA_SECCNT             5
#define MBOOT_AREA_FS                 6
#define MBOOT_AREA_INTENT             7
#define MBOOT_AREA_SHADOW             8

// Synthetic ids that are never in the area table: a sub-view of a registered area
// and the fsload stream, which has its own device id.
#define MBOOT_AREA_ID_VIEW            0x7D
#define MBOOT_AREA_ID_STREAM          0x7E
#define MBOOT_DEV_STREAM              0x7E

#endif // MICROPY_INCLUDED_SHARED_MBOOT_SYSFLASH_SYSFLASH_H
