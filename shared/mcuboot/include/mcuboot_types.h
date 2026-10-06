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

#ifndef MICROPY_INCLUDED_SHARED_MCUBOOT_MCUBOOT_TYPES_H
#define MICROPY_INCLUDED_SHARED_MCUBOOT_MCUBOOT_TYPES_H

#include <stdint.h>

// Result codes shared by the bootloader status channels (DFU status mapping, vendor
// request, update log, log text).
typedef enum {
    MCUBOOT_RES_OK = 0,
    MCUBOOT_RES_ERR_FLASH = 1,      // flash read/erase/write failure (detail = offset)
    MCUBOOT_RES_ERR_HEADER = 2,     // bad magic, header size, size fields, truncated
    MCUBOOT_RES_ERR_HASH = 3,
    MCUBOOT_RES_ERR_SIG = 4,        // no signature TLV, unknown key, signature mismatch
    MCUBOOT_RES_ERR_DOWNGRADE = 5,
    MCUBOOT_RES_ERR_TOO_BIG = 6,
    MCUBOOT_RES_ERR_NOT_TARGET = 7, // encrypted, compressed, ram-load, non-bootable flags
    MCUBOOT_RES_ERR_PENDING = 8,    // boot_set_pending failed
    MCUBOOT_RES_ERR_LAYOUT = 9,     // layout id missing/mismatch, or device map mismatch at startup
    MCUBOOT_RES_ERR_NO_IMAGE = 10,
    MCUBOOT_RES_ERR_REQUEST = 11,   // request struct or element stream malformed
    MCUBOOT_RES_ERR_TOO_SMALL = 12, // update image of at most one erase unit with swap using offset
    MCUBOOT_RES_ERR_FS_MOUNT = 16,
    MCUBOOT_RES_ERR_FS_OPEN = 17,
    MCUBOOT_RES_ERR_FS_READ = 18,
    MCUBOOT_RES_ERR_FS_GZIP = 19,
    MCUBOOT_RES_ERR_FS_PATH = 20,
} mcuboot_result_t;

// Facts parsed from an image header and its TLVs.
typedef struct {
    uint8_t valid;
    uint8_t ver_major, ver_minor;
    uint16_t ver_rev;
    uint32_t ver_build;
    uint32_t sec_cnt;       // image security counter TLV, 0 if absent
    uint32_t hash_prefix;   // first 4 bytes of the SHA-256 TLV
} mcuboot_image_info_t;

#endif // MICROPY_INCLUDED_SHARED_MCUBOOT_MCUBOOT_TYPES_H
