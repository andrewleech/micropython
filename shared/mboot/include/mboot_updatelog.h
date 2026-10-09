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

#ifndef MICROPY_INCLUDED_SHARED_MBOOT_MBOOT_UPDATELOG_H
#define MICROPY_INCLUDED_SHARED_MBOOT_MBOOT_UPDATELOG_H

#include <stdint.h>

#include "mboot_types.h"

// Append-only update audit log in MBOOT_AREA_LOG (at least two erase units, used as a ping-pong
// pair). Written by the bootloader and by the app through the same code.

#define MBOOT_LOG_REC_MAGIC 0x474C424Du   // bytes "MBLG"

typedef enum {
    LOG_BOOT_OK = 1,
    LOG_NO_IMAGE = 2,
    LOG_DFU_BEGIN = 3,
    LOG_IMAGE_ACCEPTED = 4,
    LOG_IMAGE_REJECTED = 5,
    LOG_SWAP_DONE = 6,              // test swap
    LOG_SWAP_DONE_PERM = 7,
    LOG_REVERTED = 8,
    LOG_SLOT_REJECTED_AT_BOOT = 9,  // secondary failed validation inside boot_go()
    LOG_FSLOAD_BEGIN = 10,
    LOG_FSLOAD_DONE = 11,
    LOG_FSLOAD_FAILED = 12,
    LOG_APP_CONFIRMED = 13,
    LOG_APP_UPGRADE_REQUESTED = 14,
    LOG_APP_DFU_REQUESTED = 15,
    LOG_ASSERT = 16,
    LOG_FSLOAD_RETRY = 17,
    LOG_SECCNT_FAILED = 18,         // security counter init failed: the area is damaged or unreadable
} mboot_log_type_t;

typedef enum {
    SRC_BOOT = 0,
    SRC_DFU = 1,
    SRC_FSLOAD = 2,
    SRC_APP = 3,
} mboot_log_source_t;

#define MBOOT_LOG_FLAG_PRIMARY_VALID  (1u << 0)
#define MBOOT_LOG_FLAG_SECONDARY_HDR  (1u << 1)

typedef struct __attribute__((packed)) {
    uint32_t magic;         // MBOOT_LOG_REC_MAGIC; the erased value means free slot
    uint32_t seq;           // increments by 1 per record; compare with (int32_t)(a - b)
    uint8_t type;           // mboot_log_type_t
    uint8_t result;         // mboot_result_t
    uint8_t source;         // mboot_log_source_t
    uint8_t flags;          // MBOOT_LOG_FLAG_*
    uint8_t ver_major, ver_minor;
    uint16_t ver_rev;
    uint32_t ver_build;
    uint32_t detail;        // type specific: bootutil rc, byte offset, request seq
    uint32_t hash_prefix;   // first 4 bytes of the image SHA-256 TLV when known, else 0
    uint32_t crc32;         // CRC-32/ISO-HDLC over bytes 0..27
} mboot_log_rec_t;

_Static_assert(sizeof(mboot_log_rec_t) == 32, "log record size");

// Appends one record. info may be NULL. Returns 0 or a negative errno; a failed append is
// not fatal for the caller. A record torn by a power cut never reads as valid.
int mboot_updatelog_append(uint8_t type, uint8_t result, uint8_t source, const mboot_image_info_t *info, uint32_t detail);

// Reads the n-th newest valid record (0 is the newest). Returns 0, or -ENOENT when there are
// fewer than n + 1 valid records.
int mboot_updatelog_read(uint32_t n, mboot_log_rec_t *out);

#endif // MICROPY_INCLUDED_SHARED_MBOOT_MBOOT_UPDATELOG_H
