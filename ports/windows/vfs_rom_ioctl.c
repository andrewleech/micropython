/*
 * This file is part of the MicroPython project, http://micropython.org/
 *
 * The MIT License (MIT)
 *
 * Copyright (c) 2025 Andrew Leech
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

#include "py/runtime.h"
#include "py/mperrno.h"
#include "py/objarray.h"
#include "extmod/vfs.h"

#if MICROPY_VFS_ROM_IOCTL

#include <stdio.h>

// RomFS image buffer and metadata
static const uint8_t *romfs_buf = NULL;
static size_t romfs_size = 0;
static mp_obj_t romfs_memoryview = MP_OBJ_NULL;

#if MICROPY_ROMFS_EMBEDDED
// Embedded romfs data - symbols provided by objcopy from romfs.img
// Build will fail to link if ROMFS_IMG was specified but object not provided
extern const uint8_t romfs_embedded_data[];
extern const uint8_t romfs_embedded_end[];

static void load_romfs_image(void) {
    if (romfs_buf != NULL) {
        return;
    }
    romfs_buf = romfs_embedded_data;
    romfs_size = romfs_embedded_end - romfs_embedded_data;
}

#else
// File-loading mode for development - load romfs.img from current directory
static const uint8_t empty_romfs[4] = { 0xd2, 0xcd, 0x31, 0x00 };
static uint8_t *romfs_file_buf = NULL;

static void load_romfs_image(void) {
    if (romfs_buf != NULL) {
        return;
    }

    FILE *f = fopen("romfs.img", "rb");
    if (f == NULL) {
        romfs_size = sizeof(empty_romfs);
        romfs_buf = empty_romfs;
        return;
    }

    fseek(f, 0, SEEK_END);
    long file_size = ftell(f);
    fseek(f, 0, SEEK_SET);

    if (file_size <= 0) {
        fclose(f);
        romfs_size = sizeof(empty_romfs);
        romfs_buf = empty_romfs;
        return;
    }

    // Use m_new_maybe to avoid exception on allocation failure
    romfs_file_buf = m_new_maybe(uint8_t, (size_t)file_size);
    if (romfs_file_buf == NULL) {
        fclose(f);
        romfs_size = sizeof(empty_romfs);
        romfs_buf = empty_romfs;
        return;
    }

    size_t read_size = fread(romfs_file_buf, 1, (size_t)file_size, f);
    fclose(f);

    if (read_size != (size_t)file_size) {
        m_del(uint8_t, romfs_file_buf, (size_t)file_size);
        romfs_file_buf = NULL;
        romfs_size = sizeof(empty_romfs);
        romfs_buf = empty_romfs;
        return;
    }

    romfs_buf = romfs_file_buf;
    romfs_size = (size_t)file_size;
}
#endif // MICROPY_ROMFS_EMBEDDED

mp_obj_t mp_vfs_rom_ioctl(size_t n_args, const mp_obj_t *args) {
    load_romfs_image();

    switch (mp_obj_get_int(args[0])) {
        case MP_VFS_ROM_IOCTL_GET_NUMBER_OF_SEGMENTS:
            return MP_OBJ_NEW_SMALL_INT(1);

        case MP_VFS_ROM_IOCTL_GET_SEGMENT: {
            // Create memoryview on first request
            if (romfs_memoryview == MP_OBJ_NULL) {
                mp_obj_array_t *view = MP_OBJ_TO_PTR(mp_obj_new_memoryview('B', romfs_size, (void *)romfs_buf));
                romfs_memoryview = MP_OBJ_FROM_PTR(view);
            }
            return romfs_memoryview;
        }
    }

    return MP_OBJ_NEW_SMALL_INT(-MP_EINVAL);
}

#endif // MICROPY_VFS_ROM_IOCTL
