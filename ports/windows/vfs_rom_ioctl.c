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
#include "extmod/vfs.h"

#if MICROPY_VFS_ROM_IOCTL

#include <stdio.h>

// RomFS image buffer loaded from file
static uint8_t *romfs_buf = NULL;
static size_t romfs_size = 0;
static mp_obj_t romfs_memoryview = MP_OBJ_NULL;

// Empty ROMFS header
static const uint8_t empty_romfs[4] = { 0xd2, 0xcd, 0x31, 0x00 };

// Load romfs image from file on first access
static void load_romfs_image(void) {
    if (romfs_buf != NULL) {
        return; // Already loaded
    }

    // Try to load romfs.img from the current directory
    FILE *f = fopen("romfs.img", "rb");

    if (f == NULL) {
        // No romfs.img found, use static empty ROMFS
        romfs_size = sizeof(empty_romfs);
        romfs_buf = (uint8_t *)empty_romfs;
        return;
    }

    // Get file size
    fseek(f, 0, SEEK_END);
    long file_size = ftell(f);
    fseek(f, 0, SEEK_SET);

    if (file_size <= 0) {
        // Invalid file, use empty ROMFS
        fclose(f);
        romfs_size = sizeof(empty_romfs);
        romfs_buf = (uint8_t *)empty_romfs;
        return;
    }

    romfs_size = (size_t)file_size;

    // Allocate buffer and load file
    // Use m_new_maybe to avoid exception on allocation failure
    romfs_buf = m_new_maybe(uint8_t, romfs_size);
    if (romfs_buf == NULL) {
        // Allocation failed, use empty ROMFS
        fclose(f);
        romfs_size = sizeof(empty_romfs);
        romfs_buf = (uint8_t *)empty_romfs;
        return;
    }

    size_t read_size = fread(romfs_buf, 1, romfs_size, f);
    fclose(f);

    if (read_size != romfs_size) {
        // Read error, fall back to empty ROMFS
        m_del(uint8_t, romfs_buf, romfs_size);
        romfs_size = sizeof(empty_romfs);
        romfs_buf = (uint8_t *)empty_romfs;
    }
}

mp_obj_t mp_vfs_rom_ioctl(size_t n_args, const mp_obj_t *args) {
    load_romfs_image();

    switch (mp_obj_get_int(args[0])) {
        case MP_VFS_ROM_IOCTL_GET_NUMBER_OF_SEGMENTS:
            return MP_OBJ_NEW_SMALL_INT(1);

        case MP_VFS_ROM_IOCTL_GET_SEGMENT: {
            // Create memoryview on first request
            if (romfs_memoryview == MP_OBJ_NULL) {
                mp_obj_array_t *view = MP_OBJ_TO_PTR(mp_obj_new_memoryview('B', romfs_size, romfs_buf));
                romfs_memoryview = MP_OBJ_FROM_PTR(view);
            }
            return romfs_memoryview;
        }
    }

    return MP_OBJ_NEW_SMALL_INT(-MP_EINVAL);
}

#endif // MICROPY_VFS_ROM_IOCTL
