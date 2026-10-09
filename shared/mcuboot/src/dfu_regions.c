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

// The DFU alternate settings and the flash ranges the DFU shims of dfu_glue.c may write, from
// the layout macros.

#include "mcuboot_config/mcuboot_config.h"
#include "mboot_api.h"
#include "mcuboot_dfu.h"

#define DFU_DEV MCUBOOT_SECONDARY_DEV

// The image area of the update slot: after the spare sector that swap using offset leaves at its
// start, up to the trailer. The single policy writes the only slot ("Application"), up to its
// end, the other policies the secondary slot. The sector size of a region is the erase unit of
// the area it covers.
#define DFU_IMAGE_ADDR (MCUBOOT_UPDATE_ADDR + MCUBOOT_UPDATE_SPARE)
#define DFU_TRAILER_SIZE (MCUBOOT_TRAILER_SECTORS * MCUBOOT_SLOT_UNIT)
#if MCUBOOT_POLICY_SINGLE
#define DFU_IMAGE_NAME "Application"
#else
#define DFU_IMAGE_NAME "Secondary slot"
#endif

static const mboot_region_t k_regions[] = {
    {
        .addr = DFU_IMAGE_ADDR,
        .size = MCUBOOT_UPDATE_IMAGE_SIZE,
        .sector_size = MCUBOOT_SLOT_UNIT,
        .sector_count = MCUBOOT_UPDATE_IMAGE_SIZE / MCUBOOT_SLOT_UNIT,
        .name = DFU_IMAGE_NAME,
        .flags = MBOOT_REGION_FLAG_READABLE | MBOOT_REGION_FLAG_ERASE_REQUIRED_BEFORE_WRITE,
    },
};

void mboot_port_get_regions(const mboot_region_t **regions_out, size_t *count_out) {
    *regions_out = k_regions;
    *count_out = sizeof(k_regions) / sizeof(k_regions[0]);
}

// A session_only range is erased by the session begin hook only and is not writable through
// the DFU shims: the spare sector and the trailer, which hold the swap state. The single policy
// has no swap state: its trailer sectors are in no range.
const mcuboot_wrange_t mcuboot_write_ranges[] = {
    {DFU_IMAGE_ADDR, MCUBOOT_UPDATE_IMAGE_SIZE, DFU_DEV, 0},
    #if MCUBOOT_UPDATE_SPARE != 0
    {MCUBOOT_UPDATE_ADDR, MCUBOOT_UPDATE_SPARE, DFU_DEV, 1},
    #endif
    #if !MCUBOOT_POLICY_SINGLE
    {MCUBOOT_UPDATE_END - DFU_TRAILER_SIZE, DFU_TRAILER_SIZE, DFU_DEV, 1},
    #endif
    {0, 0, 0, 0},
};
