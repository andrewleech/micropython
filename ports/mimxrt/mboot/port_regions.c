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

// port_regions.c - generic DFU region table for all mimxrt mboot boards.
//
// The board contributes MBOOT_FLASH_BASE (FlexSPI AMBA base for the chip
// family) and MICROPY_HW_FLASH_SIZE (total chip flash) via the main board's
// mpconfigboard.h and mpconfigboard.mk; MBOOT_FLASH_SIZE (the mboot
// reservation, default 64 KiB) and the region name come from the same
// inputs.  Single region from MBOOT_FLASH_BASE + MBOOT_FLASH_SIZE to end of
// flash.

#include <stddef.h>

#include "mpconfigboard.h"
#include "mboot_api.h"
#include "mboot_board.h"

#ifndef MBOOT_REGION_NAME
#define MBOOT_REGION_NAME "@" MICROPY_HW_BOARD_NAME " Flash"
#endif

#define MBOOT_REGION_SECTOR_SIZE (4096U)

static const mboot_region_t k_regions[] = {
    {
        .addr = MBOOT_FLASH_BASE + MBOOT_FLASH_SIZE,
        .size = MICROPY_HW_FLASH_SIZE - MBOOT_FLASH_SIZE,
        .sector_size = MBOOT_REGION_SECTOR_SIZE,
        .sector_count = (MICROPY_HW_FLASH_SIZE - MBOOT_FLASH_SIZE) / MBOOT_REGION_SECTOR_SIZE,
        .name = MBOOT_REGION_NAME,
        .flags = MBOOT_REGION_FLAG_READABLE | MBOOT_REGION_FLAG_ERASE_REQUIRED_BEFORE_WRITE,
    },
};

void mboot_port_get_regions(const mboot_region_t **regions_out, size_t *count_out) {
    *regions_out = k_regions;
    *count_out = sizeof(k_regions) / sizeof(k_regions[0]);
}
