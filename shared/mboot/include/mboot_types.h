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

#ifndef MICROPY_INCLUDED_SHARED_MBOOT_MBOOT_TYPES_H
#define MICROPY_INCLUDED_SHARED_MBOOT_MBOOT_TYPES_H

#include <stdint.h>

// Result codes shared by the bootloader status channels (DFU status mapping, vendor
// request, update audit log, log text).
typedef enum {
    MBOOT_RES_OK = 0,
    MBOOT_RES_ERR_FLASH = 1,      // flash read/erase/write failure (detail = offset)
    MBOOT_RES_ERR_HEADER = 2,     // bad magic, header size, size fields, truncated
    MBOOT_RES_ERR_HASH = 3,
    MBOOT_RES_ERR_SIG = 4,        // no signature TLV, unknown key, signature mismatch
    MBOOT_RES_ERR_DOWNGRADE = 5,
    MBOOT_RES_ERR_TOO_BIG = 6,
    MBOOT_RES_ERR_NOT_TARGET = 7, // encrypted, compressed, ram-load, non-bootable flags
    MBOOT_RES_ERR_PENDING = 8,    // boot_set_pending failed
    MBOOT_RES_ERR_LAYOUT = 9,     // layout id missing/mismatch, or device map mismatch at startup
    MBOOT_RES_ERR_NO_IMAGE = 10,
    MBOOT_RES_ERR_REQUEST = 11,   // request struct or element stream malformed
    MBOOT_RES_ERR_TOO_SMALL = 12, // update image of at most one erase unit with swap using offset
    MBOOT_RES_ERR_FS_MOUNT = 16,
    MBOOT_RES_ERR_FS_OPEN = 17,
    MBOOT_RES_ERR_FS_READ = 18,
    MBOOT_RES_ERR_FS_GZIP = 19,
    MBOOT_RES_ERR_FS_PATH = 20,
} mboot_result_t;

// Facts parsed from an image header and its TLVs.
typedef struct {
    uint8_t valid;
    uint8_t ver_major, ver_minor;
    uint16_t ver_rev;
    uint32_t ver_build;
    uint32_t sec_cnt;       // image security counter TLV, 0 if absent
    uint32_t hash_prefix;   // first 4 bytes of the SHA-256 TLV
} mboot_image_info_t;

#endif // MICROPY_INCLUDED_SHARED_MBOOT_MBOOT_TYPES_H
