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

#include <errno.h>
#include <stdarg.h>
#include <stdbool.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#include "flash_map_backend/flash_map_backend.h"
#include "sysflash/sysflash.h"

#include "../fake_flash.h"
#include "host_glue.h"

#define MAX_AREAS (8)

// Minimal flash_area backend over the fake flash port functions: no shadow
// words and no read policy, so ECC-invalid units reach bootutil as -EIO.
// This is the behaviour of an unmodified bootutil on an unprotected port.

static struct flash_area areas[MAX_AREAS];
static unsigned n_areas;
static FILE *log_file;
static host_halt_hook_t halt_hook;

void host_map_init(const host_area_t *a, unsigned n) {
    n_areas = n < MAX_AREAS ? n : MAX_AREAS;
    for (unsigned i = 0; i < n_areas; i++) {
        areas[i].fa_id = a[i].id;
        areas[i].fa_device_id = a[i].dev;
        areas[i].pad16 = 0;
        areas[i].fa_off = a[i].off;
        areas[i].fa_size = a[i].size;
    }
}

void host_log_set_file(FILE *f) {
    log_file = f;
}

void host_halt_set_hook(host_halt_hook_t fn) {
    halt_hook = fn;
}

void host_log(const char *level, const char *fmt, ...) {
    if (log_file == NULL) {
        return;
    }
    fprintf(log_file, "[%s] ", level);
    va_list ap;
    va_start(ap, fmt);
    vfprintf(log_file, fmt, ap);
    va_end(ap);
    fputc('\n', log_file);
    fflush(log_file);
}

void host_assert_fail(const char *file, int line) {
    host_log("ERR", "ASSERT %s:%d", file, line);
    if (halt_hook != NULL) {
        halt_hook(file, line);
    }
    _exit(66);
}

static const struct flash_area *find(uint8_t id) {
    for (unsigned i = 0; i < n_areas; i++) {
        if (areas[i].fa_id == id) {
            return &areas[i];
        }
    }
    return NULL;
}

int flash_area_open(uint8_t id, const struct flash_area **fa) {
    *fa = find(id);
    return *fa != NULL ? 0 : -ENOENT;
}

void flash_area_close(const struct flash_area *fa) {
    (void)fa;
}

static bool in_range(const struct flash_area *fa, uint32_t off, uint32_t len) {
    return off <= fa->fa_size && len <= fa->fa_size - off;
}

int flash_area_read(const struct flash_area *fa, uint32_t off, void *dst, uint32_t len) {
    if (!in_range(fa, off, len)) {
        return -EINVAL;
    }
    return mboot_port_flash_dev_read(fa->fa_device_id, fa->fa_off + off, dst, len);
}

int flash_area_write(const struct flash_area *fa, uint32_t off, const void *src, uint32_t len) {
    if (!in_range(fa, off, len)) {
        return -EINVAL;
    }
    return mboot_port_flash_dev_write(fa->fa_device_id, fa->fa_off + off, src, len);
}

int flash_area_erase(const struct flash_area *fa, uint32_t off, uint32_t len) {
    if (!in_range(fa, off, len)) {
        return -EINVAL;
    }
    return mboot_port_flash_dev_erase(fa->fa_device_id, fa->fa_off + off, len);
}

uint32_t flash_area_align(const struct flash_area *fa) {
    return fake_flash_dev_cfg(fa->fa_device_id)->write_unit;
}

uint8_t flash_area_erased_val(const struct flash_area *fa) {
    return fake_flash_dev_cfg(fa->fa_device_id)->erased_val;
}

int flash_area_get_sectors(int fa_id, uint32_t *count, struct flash_sector *sectors) {
    const struct flash_area *fa = find(fa_id);
    if (fa == NULL) {
        return -ENOENT;
    }
    uint32_t eu = fake_flash_erase_at(fa->fa_device_id, fa->fa_off);
    uint32_t n = fa->fa_size / eu;
    if (n > *count) {
        return -ENOMEM;
    }
    for (uint32_t i = 0; i < n; i++) {
        sectors[i].fs_off = i * eu;
        sectors[i].fs_size = eu;
    }
    *count = n;
    return 0;
}

int flash_area_get_sector(const struct flash_area *fa, off_t off, struct flash_sector *fs) {
    if (off < 0 || (uint32_t)off >= fa->fa_size) {
        return -ERANGE;
    }
    uint32_t eu = fake_flash_erase_at(fa->fa_device_id, fa->fa_off);
    fs->fs_off = ((uint32_t)off / eu) * eu;
    fs->fs_size = eu;
    return 0;
}

int flash_area_id_from_multi_image_slot(int image_index, int slot) {
    (void)image_index;
    switch (slot) {
        case 0:
            return FLASH_AREA_IMAGE_PRIMARY(0);
        case 1:
            return FLASH_AREA_IMAGE_SECONDARY(0);
    }
    return -EINVAL;
}

int flash_area_id_from_image_slot(int slot) {
    return flash_area_id_from_multi_image_slot(0, slot);
}

int flash_device_base(uint8_t fd_id, uintptr_t *ret) {
    (void)fd_id;
    *ret = 0;
    return 0;
}
