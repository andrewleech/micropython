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

// port_regions.c - generic DFU region table for all rp2 mboot boards.
//
// Single region: from XIP_BASE + MBOOT_RESERVED to end of flash.  The board
// only contributes PICO_FLASH_SIZE_BYTES (already provided by pico-sdk for
// every rp2 board) and its display name via MICROPY_HW_BOARD_NAME.  Boards
// that want a different region name may #define MBOOT_REGION_NAME.

#include <stddef.h>
#include "hardware/flash.h"
#include "hardware/regs/addressmap.h"
#include "mpconfigboard.h"
#include "mboot_api.h"
#include "mboot_board.h"

#ifndef MBOOT_REGION_NAME
#define MBOOT_REGION_NAME "@" MICROPY_HW_BOARD_NAME " Flash"
#endif

static const mboot_region_t k_regions[] = {
    {
        .addr = XIP_BASE + MBOOT_RESERVED,
        .size = PICO_FLASH_SIZE_BYTES - MBOOT_RESERVED,
        .sector_size = FLASH_SECTOR_SIZE,
        .sector_count = (PICO_FLASH_SIZE_BYTES - MBOOT_RESERVED) / FLASH_SECTOR_SIZE,
        .name = MBOOT_REGION_NAME,
        .flags = MBOOT_REGION_FLAG_READABLE | MBOOT_REGION_FLAG_ERASE_REQUIRED_BEFORE_WRITE,
    },
};

void mboot_port_get_regions(const mboot_region_t **regions_out, size_t *count_out) {
    *regions_out = k_regions;
    *count_out = sizeof(k_regions) / sizeof(k_regions[0]);
}
