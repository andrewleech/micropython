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

// The flash devices and areas of the build, from the macros of mcuboot_layout.h.

#include "mcuboot_config/mcuboot_config.h"
#include "mcuboot_port.h"
#include "flash_map_backend/flash_map_backend.h"
#include "sysflash/sysflash.h"

const mcuboot_flash_dev_t mcuboot_devs[] = {
    {
        .id = 0,
        .erased_val = MCUBOOT_DEV0_ERASED_VAL,
        .mapped = MCUBOOT_DEV0_MAPPED,
        .write_unit = MCUBOOT_DEV0_WRITE,
        .base = MCUBOOT_DEV0_BASE,
        .size = MCUBOOT_DEV0_SIZE,
        .run_count = MCUBOOT_DEV0_RUNS,
        .runs = {
            {MCUBOOT_DEV0_RUN0_SIZE, MCUBOOT_DEV0_RUN0_ERASE},
            {MCUBOOT_DEV0_RUN1_SIZE, MCUBOOT_DEV0_RUN1_ERASE},
            {MCUBOOT_DEV0_RUN2_SIZE, MCUBOOT_DEV0_RUN2_ERASE},
            {MCUBOOT_DEV0_RUN3_SIZE, MCUBOOT_DEV0_RUN3_ERASE},
        },
        .name = MCUBOOT_DEV0_NAME,
    },
    #if defined(MCUBOOT_DEV1_BASE)
    {
        .id = 1,
        .erased_val = MCUBOOT_DEV1_ERASED_VAL,
        .mapped = MCUBOOT_DEV1_MAPPED,
        .write_unit = MCUBOOT_DEV1_WRITE,
        .base = MCUBOOT_DEV1_BASE,
        .size = MCUBOOT_DEV1_SIZE,
        .run_count = MCUBOOT_DEV1_RUNS,
        .runs = {
            {MCUBOOT_DEV1_RUN0_SIZE, MCUBOOT_DEV1_RUN0_ERASE},
            {MCUBOOT_DEV1_RUN1_SIZE, MCUBOOT_DEV1_RUN1_ERASE},
            {MCUBOOT_DEV1_RUN2_SIZE, MCUBOOT_DEV1_RUN2_ERASE},
            {MCUBOOT_DEV1_RUN3_SIZE, MCUBOOT_DEV1_RUN3_ERASE},
        },
        .name = MCUBOOT_DEV1_NAME,
    },
    #endif
};
const unsigned mcuboot_dev_count = sizeof(mcuboot_devs) / sizeof(mcuboot_devs[0]);

#define AREA(id, addr, size) \
    { \
        .fa_id = (id), \
        .fa_device_id = MCUBOOT_DEV_OF(addr), \
        .fa_off = (addr) - MCUBOOT_DEV_BASE(MCUBOOT_DEV_OF(addr)), \
        .fa_size = (size), \
    }

const struct flash_area mcuboot_areas[] = {
    AREA(FLASH_AREA_BOOTLOADER, MCUBOOT_BOOT_ADDR, MCUBOOT_BOOT_SIZE),
    AREA(FLASH_AREA_IMAGE_PRIMARY(0), MCUBOOT_PRIMARY_ADDR, MCUBOOT_PRIMARY_SIZE),
    #if !MCUBOOT_POLICY_SINGLE
    AREA(FLASH_AREA_IMAGE_SECONDARY(0), MCUBOOT_SECONDARY_ADDR, MCUBOOT_SECONDARY_SIZE),
    #endif
    #if defined(MCUBOOT_SWAP_USING_SCRATCH)
    AREA(FLASH_AREA_IMAGE_SCRATCH, MCUBOOT_SCRATCH_ADDR, MCUBOOT_SCRATCH_SIZE),
    #endif
    #if defined(MCUBOOT_SECCNT_FLASH)
    AREA(MCUBOOT_AREA_SECCNT, MCUBOOT_SECCNT_ADDR, MCUBOOT_SECCNT_SIZE),
    #endif
    #if defined(MCUBOOT_ECC_SHADOW)
    AREA(MCUBOOT_AREA_SHADOW, MCUBOOT_SHADOW_ADDR, MCUBOOT_SHADOW_SIZE),
    #endif
    #if defined(MCUBOOT_INTENT_ADDR)
    AREA(MCUBOOT_AREA_INTENT, MCUBOOT_INTENT_ADDR, MCUBOOT_INTENT_SIZE),
    #endif
    AREA(MCUBOOT_AREA_FS, MCUBOOT_FS_ADDR, MCUBOOT_FS_SIZE),
};
const unsigned mcuboot_area_count = sizeof(mcuboot_areas) / sizeof(mcuboot_areas[0]);
