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
#include <string.h>

#include "bootutil/image.h"
#include "flash_map_backend/flash_map_backend.h"
#include "mcuboot_fsload.h"
#include "mcuboot_updatelog.h"
#include "mcuboot_validate.h"
#include "stub_fuzz.h"

// Doubles for the parts of the bootloader that fsload calls but that are not part of fsload: the
// update log, the bootutil pending call and the validator. The validator only checks structure
// (header, TLV chain). It reads the whole image through the stream area in the access pattern of
// bootutil, so every byte of a stream goes through the wrapper under the sanitizers. It does not
// hash or verify signatures.

stub_state_t stub_state;

int mcuboot_updatelog_append(uint8_t type, uint8_t result, uint8_t source, const mcuboot_image_info_t *info, uint32_t detail) {
    (void)source;
    (void)info;
    (void)detail;
    stub_state.log_records++;
    stub_state.last_type = type;
    stub_state.last_result = result;
    return 0;
}

int boot_set_pending_multi(int image_index, int permanent) {
    stub_state.pending_calls++;
    stub_state.pending_permanent = permanent;
    return stub_state.pending_fail ? -1 : image_index;
}

static uint32_t le32(const uint8_t *p) {
    return p[0] | (uint32_t)p[1] << 8 | (uint32_t)p[2] << 16 | (uint32_t)p[3] << 24;
}

static uint16_t le16(const uint8_t *p) {
    return p[0] | (uint16_t)p[1] << 8;
}

mcuboot_validate_result_t mcuboot_validate_view(const struct flash_area *fap, uint32_t flags) {
    mcuboot_validate_result_t r;
    uint8_t buf[256];
    memset(&r, 0, sizeof(r));
    (void)flags;

    if (flash_area_read(fap, 0, buf, IMAGE_HEADER_SIZE) != 0) {
        r.code = MCUBOOT_RES_ERR_FLASH;
        return r;
    }
    uint32_t hdr_size = le16(buf + 8);
    uint32_t prot = le16(buf + 10);
    uint32_t img = le32(buf + 12);
    uint32_t end = hdr_size;
    if (le32(buf) != IMAGE_MAGIC || __builtin_add_overflow(end, img, &end) || __builtin_add_overflow(end, prot, &end) ||
        end >= fap->fa_size) {
        r.code = MCUBOOT_RES_ERR_HEADER;
        return r;
    }

    // Body in chunks, as bootutil_img_hash() does.
    uint32_t sum = 0;
    for (uint32_t off = 0; off < end;) {
        uint32_t n = end - off < sizeof(buf) ? end - off : sizeof(buf);
        if (flash_area_read(fap, off, buf, n) != 0) {
            r.code = MCUBOOT_RES_ERR_FLASH;
            r.detail = off;
            return r;
        }
        for (uint32_t i = 0; i < n; ++i) {
            sum += buf[i];
        }
        off += n;
    }
    stub_state.last_sum = sum;

    // TLV info and entries, then the header again (bootutil reads both a second time for the
    // version and security counter).
    if (flash_area_read(fap, end, buf, 4) != 0) {
        r.code = MCUBOOT_RES_ERR_FLASH;
        return r;
    }
    uint32_t tlv_end = end;
    if (le16(buf) != IMAGE_TLV_INFO_MAGIC || __builtin_add_overflow(tlv_end, le16(buf + 2), &tlv_end) || tlv_end > fap->fa_size) {
        r.code = MCUBOOT_RES_ERR_HEADER;
        return r;
    }
    for (uint32_t off = end + 4; off < tlv_end;) {
        if (tlv_end - off < 4 || flash_area_read(fap, off, buf, 4) != 0) {
            r.code = MCUBOOT_RES_ERR_HEADER;
            return r;
        }
        uint32_t len = le16(buf + 2);
        if (len > tlv_end - off - 4) {
            r.code = MCUBOOT_RES_ERR_HEADER;
            return r;
        }
        off += 4 + len;
    }
    if (flash_area_read(fap, 0, buf, IMAGE_HEADER_SIZE) != 0) {
        r.code = MCUBOOT_RES_ERR_FLASH;
        return r;
    }
    r.info.valid = 1;
    r.code = MCUBOOT_RES_OK;
    return r;
}

void mcuboot_fsload_status_store(uint32_t addr, uint32_t value) {
    stub_state.status_stores++;
    stub_state.status_addr = addr;
    stub_state.status_value = value;
}
