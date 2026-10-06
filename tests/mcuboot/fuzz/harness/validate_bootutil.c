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

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "bootutil/bootutil.h"
#include "bootutil/bootutil_public.h"
#include "bootutil/fault_injection_hardening.h"
#include "bootutil/image.h"
#include "bootutil/sign_key.h"
#include "bootutil_priv.h"
#include "flash_map_backend/flash_map_backend.h"
#include "mcuboot_log.h"
#include "mcuboot_validate.h"
#include "sysflash/sysflash.h"
#include "tinycrypt/sha256.h"

// Host validator for test_fsload. It runs the real bootutil_img_validate() (hash, signature with
// tinycrypt ECDSA P-256) over the area it is given, as mcuboot_validate_view() does in the
// bootloader. Swap using offset needs a populated boot_loader_state, which is built from the
// primary and secondary areas; with the single slot policy the state is NULL.
//
// bootutil_img_validate() only answers success or failure, so the hash is checked first, with the
// TLV walk in this file, to tell MCUBOOT_RES_ERR_HASH from MCUBOOT_RES_ERR_SIG. That check is
// compiled out with VALIDATE_NO_CLASSIFY, to measure the reads of bootutil alone. The
// VALIDATE_CHECK_TARGET checks are the flag and header size checks; the layout id and the
// downgrade check are not done here.

extern const unsigned char ecdsa_pub_key[];
extern const unsigned int ecdsa_pub_key_len;

const struct bootutil_key bootutil_keys[] = {
    {
        .key = ecdsa_pub_key,
        .len = &ecdsa_pub_key_len,
    },
};
const int bootutil_key_cnt = 1;

// tinycrypt's ecc.c names it in a static initialiser. Verification draws no random numbers, and
// an optimised build drops the reference.
int default_CSPRNG(uint8_t *dest, unsigned int size) {
    (void)dest;
    (void)size;
    return 0;
}

void mcuboot_assert_fail(const char *file, int line) {
    fprintf(stderr, "ASSERT %s:%d\n", file, line);
    abort();
}

void mcuboot_fault_recover(void) {
    abort();
}

static uint32_t le32(const uint8_t *p) {
    return p[0] | (uint32_t)p[1] << 8 | (uint32_t)p[2] << 16 | (uint32_t)p[3] << 24;
}

static uint16_t le16(const uint8_t *p) {
    return p[0] | (uint16_t)p[1] << 8;
}

// Compares the SHA-256 of header, image and protected TLVs with the hash TLV. Returns 0 on a
// match, 1 on a mismatch or a missing TLV, -1 on a read error.
static int check_hash(const struct flash_area *fap, const struct image_header *hdr, uint32_t *detail) {
    struct tc_sha256_state_struct sha;
    uint8_t buf[256];
    uint8_t digest[TC_SHA256_DIGEST_SIZE];
    uint32_t end = hdr->ih_hdr_size + hdr->ih_img_size + hdr->ih_protect_tlv_size;

    tc_sha256_init(&sha);
    for (uint32_t off = 0; off < end;) {
        uint32_t n = end - off < sizeof(buf) ? end - off : sizeof(buf);
        if (flash_area_read(fap, off, buf, n) != 0) {
            *detail = off;
            return -1;
        }
        tc_sha256_update(&sha, buf, n);
        off += n;
    }
    tc_sha256_final(digest, &sha);

    // TLV area: the protected info (if any), then the unprotected info and entries.
    uint32_t off = hdr->ih_hdr_size + hdr->ih_img_size;
    uint8_t info[4];
    if (flash_area_read(fap, off, info, 4) != 0) {
        return -1;
    }
    if (le16(info) == IMAGE_TLV_PROT_INFO_MAGIC) {
        off += le16(info + 2);
        if (flash_area_read(fap, off, info, 4) != 0) {
            return -1;
        }
    }
    if (le16(info) != IMAGE_TLV_INFO_MAGIC) {
        return 1;
    }
    uint32_t tlv_end = off + le16(info + 2);
    for (off += 4; off + 4 <= tlv_end;) {
        uint8_t tl[4];
        if (flash_area_read(fap, off, tl, 4) != 0) {
            return -1;
        }
        uint32_t len = le16(tl + 2);
        if (le16(tl) == IMAGE_TLV_SHA256 && len == sizeof(digest)) {
            if (flash_area_read(fap, off + 4, buf, len) != 0) {
                return -1;
            }
            return memcmp(buf, digest, len) == 0 ? 0 : 1;
        }
        off += 4 + len;
    }
    return 1;
}

mcuboot_validate_result_t mcuboot_validate_view(const struct flash_area *fap, uint32_t flags) {
    static uint8_t tmpbuf[BOOT_TMPBUF_SZ];
    mcuboot_validate_result_t r;
    struct image_header hdr;
    struct boot_loader_state *state = NULL;
    memset(&r, 0, sizeof(r));

    #if !defined(MCUBOOT_SINGLE_APPLICATION_SLOT)
    static struct boot_loader_state state_storage;
    boot_state_init(&state_storage);
    if (flash_area_open(FLASH_AREA_IMAGE_PRIMARY(0), &BOOT_IMG_AREA(&state_storage, BOOT_SLOT_PRIMARY)) != 0 ||
        flash_area_open(FLASH_AREA_IMAGE_SECONDARY(0), &BOOT_IMG_AREA(&state_storage, BOOT_SLOT_SECONDARY)) != 0 ||
        boot_read_sectors(&state_storage, NULL) != 0) {
        r.code = MCUBOOT_RES_ERR_FLASH;
        return r;
    }
    state = &state_storage;
    #endif

    int rc = boot_image_load_header(fap, &hdr);
    if (rc != 0) {
        r.code = rc == BOOT_EFLASH ? MCUBOOT_RES_ERR_FLASH : MCUBOOT_RES_ERR_HEADER;
        r.detail = rc;
        return r;
    }
    if (flags & VALIDATE_CHECK_TARGET) {
        if ((hdr.ih_flags & (IMAGE_F_ENCRYPTED_AES128 | IMAGE_F_ENCRYPTED_AES256 | IMAGE_F_COMPRESSED_LZMA1 |
            IMAGE_F_COMPRESSED_LZMA2 | IMAGE_F_COMPRESSED_ARM_THUMB_FLT | IMAGE_F_RAM_LOAD)) != 0 ||
            hdr.ih_hdr_size != MCUBOOT_HEADER_SIZE) {
            r.code = MCUBOOT_RES_ERR_NOT_TARGET;
            return r;
        }
    }

    #if !defined(VALIDATE_NO_CLASSIFY)
    int h = check_hash(fap, &hdr, &r.detail);
    if (h < 0) {
        r.code = MCUBOOT_RES_ERR_FLASH;
        return r;
    }
    if (h > 0) {
        r.code = MCUBOOT_RES_ERR_HASH;
        return r;
    }
    #endif

    FIH_DECLARE(fih_rc, FIH_FAILURE);
    FIH_CALL(bootutil_img_validate, fih_rc, state, &hdr, fap, tmpbuf, sizeof(tmpbuf), NULL, 0, NULL);
    if (FIH_NOT_EQ(fih_rc, FIH_SUCCESS)) {
        r.code = MCUBOOT_RES_ERR_SIG;
        return r;
    }
    r.info.valid = 1;
    r.info.ver_major = hdr.ih_ver.iv_major;
    r.info.ver_minor = hdr.ih_ver.iv_minor;
    r.info.ver_rev = hdr.ih_ver.iv_revision;
    r.info.ver_build = hdr.ih_ver.iv_build_num;
    r.code = MCUBOOT_RES_OK;
    return r;
}
