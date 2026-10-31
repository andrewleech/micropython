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


#ifndef MICROPY_INCLUDED_SHARED_MBOOT_MBOOT_BL_INFO_H
#define MICROPY_INCLUDED_SHARED_MBOOT_MBOOT_BL_INFO_H

#include <stddef.h>
#include <stdint.h>

// Bootloader info block: the last MBOOT_BL_INFO_SIZE bytes of the boot area, written by the
// bootloader build (src/boot_info.c, section .mboot_info) and read by the application through
// mboot_app_bootloader_info(). Little endian.
//
//   off  size
//     0     4  magic, MBOOT_BL_INFO_MAGIC
//     4     2  info_version, MBOOT_BL_INFO_VERSION
//     6     2  size: bytes from the start of the block that hold defined fields
//     8     4  flags, MBOOT_BL_INFO_F_*
//    12     4  layout_id, MBOOT_LAYOUT_ID of the bootloader build
//    16     4  api_version, MBOOT_API_VERSION of the bootloader build
//    20    24  version string, NUL padded (not terminated when it fills the field)
//    44     4  bl_image_id: security counter image id of the bootloader
//    48     4  bl_counter: security counter value of the running bootloader
//    52    32  key_hash: SHA-256 of the trusted public key, as in the image key hash TLV
//    84    44  reserved, 0xFF
//
// A later bootloader adds fields in the reserved bytes and raises size; a reader takes a field
// that ends beyond size as absent.

#define MBOOT_BL_INFO_SIZE      (128)
#define MBOOT_BL_INFO_MAGIC     (0x4E49424Du)    // bytes "MBIN"
#define MBOOT_BL_INFO_VERSION   (1)

// Flags.
#define MBOOT_BL_INFO_F_DFU             (1u << 0)   // DFU front end
#define MBOOT_BL_INFO_F_FSLOAD          (1u << 1)   // fsload front end
#define MBOOT_BL_INFO_F_GZIP            (1u << 2)   // fsload accepts gzip files
#define MBOOT_BL_INFO_F_UPDATE_SHIFT    (3)         // 2 bits, bootloader update method:
#define MBOOT_BL_INFO_F_UPDATE_MASK     (3u << MBOOT_BL_INFO_F_UPDATE_SHIFT)
#define MBOOT_BL_INFO_UPDATE_NONE       (0)
#define MBOOT_BL_INFO_UPDATE_IN_PLACE   (1)
#define MBOOT_BL_INFO_UPDATE_DUAL_COPY  (2)
// Reserved for the opt-in features, 0 until a feature sets them.
#define MBOOT_BL_INFO_F_KEY_HW          (1u << 5)   // key hash from hardware instead of built in
#define MBOOT_BL_INFO_F_TRUSTZONE       (1u << 6)   // bootloader resident in the secure world
#define MBOOT_BL_INFO_F_ENC_IMAGES      (1u << 7)   // encrypted images accepted

typedef struct {
    uint32_t magic;
    uint16_t info_version;
    uint16_t size;
    uint32_t flags;
    uint32_t layout_id;
    uint32_t api_version;
    char version[24];
    uint32_t bl_image_id;
    uint32_t bl_counter;
    uint8_t key_hash[32];
    uint8_t reserved[44];
} mboot_bl_info_t;

// Bytes of the defined fields, the value of size written by this bootloader.
#define MBOOT_BL_INFO_DEFINED   (84)

_Static_assert(sizeof(mboot_bl_info_t) == MBOOT_BL_INFO_SIZE, "bootloader info block size");
_Static_assert(offsetof(mboot_bl_info_t, reserved) == MBOOT_BL_INFO_DEFINED, "bootloader info block fields");

#endif // MICROPY_INCLUDED_SHARED_MBOOT_MBOOT_BL_INFO_H
