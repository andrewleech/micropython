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

// The flash devices and areas of the build, from the macros of mboot_layout.h.

#include "mcuboot_config/mcuboot_config.h"
#include "mboot_port.h"
#include "flash_map_backend/flash_map_backend.h"
#include "sysflash/sysflash.h"

const mboot_flash_dev_t mboot_devs[] = {
    {
        .id = 0,
        .erased_val = MBOOT_DEV0_ERASED_VAL,
        .mapped = MBOOT_DEV0_MAPPED,
        .write_unit = MBOOT_DEV0_WRITE,
        .base = MBOOT_DEV0_BASE,
        .size = MBOOT_DEV0_SIZE,
        .run_count = MBOOT_DEV0_RUNS,
        .runs = {
            {MBOOT_DEV0_RUN0_SIZE, MBOOT_DEV0_RUN0_ERASE},
            {MBOOT_DEV0_RUN1_SIZE, MBOOT_DEV0_RUN1_ERASE},
            {MBOOT_DEV0_RUN2_SIZE, MBOOT_DEV0_RUN2_ERASE},
            {MBOOT_DEV0_RUN3_SIZE, MBOOT_DEV0_RUN3_ERASE},
        },
        .name = MBOOT_DEV0_NAME,
    },
    #if defined(MBOOT_DEV1_BASE)
    {
        .id = 1,
        .erased_val = MBOOT_DEV1_ERASED_VAL,
        .mapped = MBOOT_DEV1_MAPPED,
        .write_unit = MBOOT_DEV1_WRITE,
        .base = MBOOT_DEV1_BASE,
        .size = MBOOT_DEV1_SIZE,
        .run_count = MBOOT_DEV1_RUNS,
        .runs = {
            {MBOOT_DEV1_RUN0_SIZE, MBOOT_DEV1_RUN0_ERASE},
            {MBOOT_DEV1_RUN1_SIZE, MBOOT_DEV1_RUN1_ERASE},
            {MBOOT_DEV1_RUN2_SIZE, MBOOT_DEV1_RUN2_ERASE},
            {MBOOT_DEV1_RUN3_SIZE, MBOOT_DEV1_RUN3_ERASE},
        },
        .name = MBOOT_DEV1_NAME,
    },
    #endif
};
const unsigned mboot_dev_count = sizeof(mboot_devs) / sizeof(mboot_devs[0]);

#define AREA(id, addr, size) \
    { \
        .fa_id = (id), \
        .fa_device_id = MBOOT_DEV_OF(addr), \
        .fa_off = (addr) - MBOOT_DEV_BASE(MBOOT_DEV_OF(addr)), \
        .fa_size = (size), \
    }

const struct flash_area mboot_areas[] = {
    AREA(FLASH_AREA_BOOTLOADER, MBOOT_BOOT_ADDR, MBOOT_BOOT_SIZE),
    AREA(FLASH_AREA_IMAGE_PRIMARY(0), MBOOT_PRIMARY_ADDR, MBOOT_PRIMARY_SIZE),
    #if !MBOOT_POLICY_SINGLE
    AREA(FLASH_AREA_IMAGE_SECONDARY(0), MBOOT_SECONDARY_ADDR, MBOOT_SECONDARY_SIZE),
    #endif
    #if defined(MCUBOOT_SWAP_USING_SCRATCH)
    AREA(FLASH_AREA_IMAGE_SCRATCH, MBOOT_SCRATCH_ADDR, MBOOT_SCRATCH_SIZE),
    #endif
    AREA(MBOOT_AREA_LOG, MBOOT_LOG_ADDR, MBOOT_LOG_SIZE),
    #if defined(MBOOT_SECCNT_FLASH)
    AREA(MBOOT_AREA_SECCNT, MBOOT_SECCNT_ADDR, MBOOT_SECCNT_SIZE),
    #endif
    #if defined(MBOOT_ECC_SHADOW)
    AREA(MBOOT_AREA_SHADOW, MBOOT_SHADOW_ADDR, MBOOT_SHADOW_SIZE),
    #endif
    #if defined(MBOOT_INTENT_ADDR)
    AREA(MBOOT_AREA_INTENT, MBOOT_INTENT_ADDR, MBOOT_INTENT_SIZE),
    #endif
    AREA(MBOOT_AREA_FS, MBOOT_FS_ADDR, MBOOT_FS_SIZE),
};
const unsigned mboot_area_count = sizeof(mboot_areas) / sizeof(mboot_areas[0]);
