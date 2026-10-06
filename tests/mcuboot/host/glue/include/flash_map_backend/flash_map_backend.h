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

#ifndef MICROPY_INCLUDED_TESTS_MCUBOOT_HOST_GLUE_FLASH_MAP_BACKEND_H
#define MICROPY_INCLUDED_TESTS_MCUBOOT_HOST_GLUE_FLASH_MAP_BACKEND_H

#include <stddef.h>
#include <stdint.h>
#include <sys/types.h>

struct flash_area {
    uint8_t fa_id;
    uint8_t fa_device_id;
    uint16_t pad16;
    uint32_t fa_off;
    uint32_t fa_size;
};

struct flash_sector {
    uint32_t fs_off;
    uint32_t fs_size;
};

static inline uint8_t flash_area_get_id(const struct flash_area *fa) {
    return fa->fa_id;
}

static inline uint8_t flash_area_get_device_id(const struct flash_area *fa) {
    return fa->fa_device_id;
}

static inline uint32_t flash_area_get_off(const struct flash_area *fa) {
    return fa->fa_off;
}

static inline uint32_t flash_area_get_size(const struct flash_area *fa) {
    return fa->fa_size;
}

static inline uint32_t flash_sector_get_off(const struct flash_sector *fs) {
    return fs->fs_off;
}

static inline uint32_t flash_sector_get_size(const struct flash_sector *fs) {
    return fs->fs_size;
}

int flash_area_open(uint8_t id, const struct flash_area **fa);
void flash_area_close(const struct flash_area *fa);
int flash_area_read(const struct flash_area *fa, uint32_t off, void *dst, uint32_t len);
int flash_area_write(const struct flash_area *fa, uint32_t off, const void *src, uint32_t len);
int flash_area_erase(const struct flash_area *fa, uint32_t off, uint32_t len);
uint32_t flash_area_align(const struct flash_area *fa);
uint8_t flash_area_erased_val(const struct flash_area *fa);
int flash_area_get_sectors(int fa_id, uint32_t *count, struct flash_sector *sectors);
int flash_area_get_sector(const struct flash_area *fa, off_t off, struct flash_sector *fs);
int flash_area_id_from_multi_image_slot(int image_index, int slot);
int flash_area_id_from_image_slot(int slot);
int flash_device_base(uint8_t fd_id, uintptr_t *ret);

#endif // MICROPY_INCLUDED_TESTS_MCUBOOT_HOST_GLUE_FLASH_MAP_BACKEND_H
