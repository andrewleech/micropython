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
#include <stdbool.h>
#include <string.h>

#include "bootutil/bootutil.h"
#include "bootutil/bootutil_public.h"
#include "bootutil/fault_injection_hardening.h"
#include "bootutil/image.h"
#include "bootutil_loader.h"
#include "bootutil_priv.h"
#include "flash_map_backend/flash_map_backend.h"
#include "mcuboot_config/mcuboot_config.h"
#include "mcuboot_validate.h"
#include "sysflash/sysflash.h"

#if defined(MCUBOOT_HW_ROLLBACK_PROT)
#include "bootutil/security_cnt.h"
#endif

// Image validation outside boot_go(): runs the real bootutil_img_validate() over a populated
// boot_loader_state so app_max_size() works in every swap mode, with the pre-checks and error
// classification (mcuboot_result_t) that the DFU and fsload status reports need.

// Protected TLV carrying the 4-byte layout id the image was linked for (added by
// tools/mcuboot_sign.py).
#define LAYOUT_TLV 0x00A1u
#define LAYOUT_ID_LEN 4u

// Length of the SHA-256 digest of the image (the only hash of the supported signature scheme).
#define IMG_HASH_LEN 32u

// Algorithms whose maximum image size comes from the sector tables of the state.
#if defined(MCUBOOT_SWAP_USING_OFFSET) || defined(MCUBOOT_SWAP_USING_MOVE) || defined(MCUBOOT_SWAP_USING_SCRATCH)
#define VALIDATE_NEEDS_STATE 1
#else
#define VALIDATE_NEEDS_STATE 0
#endif

static uint8_t s_tmpbuf[BOOT_TMPBUF_SZ];

#if VALIDATE_NEEDS_STATE
static struct boot_loader_state s_state;

// A state with the areas and sector counts of the slots and nothing else. secondary_offset
// stays 0: the areas handed to the validator are views or streams that start at the image, so
// boot_get_state_secondary_offset() also answers 0 for them.
static int state_init(void) {
    boot_state_init(&s_state);
    if (flash_area_open(FLASH_AREA_IMAGE_PRIMARY(0), &BOOT_IMG_AREA(&s_state, BOOT_SLOT_PRIMARY)) != 0
        || flash_area_open(FLASH_AREA_IMAGE_SECONDARY(0), &BOOT_IMG_AREA(&s_state, BOOT_SLOT_SECONDARY)) != 0) {
        return -ENOENT;
    }
    #if defined(MCUBOOT_SWAP_USING_SCRATCH)
    if (flash_area_open(FLASH_AREA_IMAGE_SCRATCH, &s_state.scratch.area) != 0) {
        return -ENOENT;
    }
    #endif
    int rc = boot_read_sectors(&s_state, NULL);
    return rc;
}
#endif

// The registered secondary slot holds its image one erase unit in (swap using offset). The
// validator reads an image from offset 0 of the area it is given, so the slot is replaced by the
// view the DFU front end uses.
static const struct flash_area *update_view(const struct flash_area *fap) {
    #if defined(MCUBOOT_SWAP_USING_OFFSET)
    static struct flash_area view;
    if (fap->fa_id == FLASH_AREA_IMAGE_SECONDARY(0) && fap->fa_device_id != MCUBOOT_DEV_STREAM) {
        uint32_t spare = mcuboot_area_erase(fap);
        view = *fap;
        view.fa_id = MCUBOOT_AREA_ID_VIEW;
        view.fa_off += spare;
        view.fa_size -= spare;
        return &view;
    }
    #endif
    return fap;
}

// Looks for one TLV of the image. Returns 0 and fills buf (len bytes, the TLV must have
// exactly that length), 1 if there is no such TLV, or a negative value for a malformed TLV
// area or a read failure.
static int tlv_find(const struct flash_area *fap, const struct image_header *hdr, uint16_t type, bool prot, void *buf, uint16_t len) {
    struct image_tlv_iter it;
    uint32_t off;
    uint16_t tlv_len;
    #if defined(MCUBOOT_SWAP_USING_OFFSET)
    it.start_off = 0;
    #endif
    if (bootutil_tlv_iter_begin(&it, hdr, fap, type, prot) != 0) {
        return -1;
    }
    int rc = bootutil_tlv_iter_next(&it, &off, &tlv_len, NULL);
    if (rc != 0) {
        return rc;
    }
    if (tlv_len != len || flash_area_read(fap, off, buf, len) != 0) {
        return -1;
    }
    return 0;
}

static uint32_t get_le32(const uint8_t *p) {
    return p[0] | (uint32_t)p[1] << 8 | (uint32_t)p[2] << 16 | (uint32_t)p[3] << 24;
}

static uint32_t get_be32(const uint8_t *p) {
    return (uint32_t)p[0] << 24 | (uint32_t)p[1] << 16 | (uint32_t)p[2] << 8 | p[3];
}

// Version, security counter and hash prefix of an image whose header has been read.
static void info_fill(const struct flash_area *fap, const struct image_header *hdr, mcuboot_image_info_t *info) {
    uint8_t buf[IMG_HASH_LEN];
    memset(info, 0, sizeof(*info));
    info->valid = 1;
    info->ver_major = hdr->ih_ver.iv_major;
    info->ver_minor = hdr->ih_ver.iv_minor;
    info->ver_rev = hdr->ih_ver.iv_revision;
    info->ver_build = hdr->ih_ver.iv_build_num;
    if (tlv_find(fap, hdr, IMAGE_TLV_SEC_CNT, true, buf, 4) == 0) {
        info->sec_cnt = get_le32(buf);
    }
    if (tlv_find(fap, hdr, IMAGE_TLV_SHA256, false, buf, IMG_HASH_LEN) == 0) {
        info->hash_prefix = get_be32(buf);
    }
}

void mcuboot_image_info_read(const struct flash_area *fap, mcuboot_image_info_t *info) {
    struct image_header hdr;
    memset(info, 0, sizeof(*info));
    fap = update_view(fap);
    if (flash_area_read(fap, 0, &hdr, sizeof(hdr)) != 0 || hdr.ih_magic != IMAGE_MAGIC) {
        return;
    }
    info_fill(fap, &hdr, info);
}

// Offset of the first byte of [off, off + len) that cannot be read, or -1 when all of it can.
static int64_t first_read_failure(const struct flash_area *fap, uint32_t off, uint32_t len) {
    uint32_t end = off + len;
    while (off < end) {
        uint32_t n = end - off < sizeof(s_tmpbuf) ? end - off : (uint32_t)sizeof(s_tmpbuf);
        if (flash_area_read(fap, off, s_tmpbuf, n) != 0) {
            // Narrow it down to the unit within the chunk.
            uint32_t unit = flash_area_align(fap);
            for (uint32_t o = off; o < off + n; o += unit) {
                if (flash_area_read(fap, o, s_tmpbuf, unit < off + n - o ? unit : off + n - o) != 0) {
                    return o;
                }
            }
            return off;
        }
        off += n;
    }
    return -1;
}

#if defined(MCUBOOT_HW_ROLLBACK_PROT)
// The stored security counter. Returns false when it cannot be read.
static bool stored_counter(uint32_t *value) {
    FIH_DECLARE(fih_rc, FIH_FAILURE);
    fih_int stored;
    FIH_CALL(boot_nv_security_counter_get, fih_rc, 0, &stored);
    if (FIH_NOT_EQ(fih_rc, FIH_SUCCESS)) {
        return false;
    }
    *value = (uint32_t)fih_int_decode(stored);
    return true;
}

// True if the image carries no security counter or one below the stored value.
static bool counter_rejected(const struct flash_area *fap, const struct image_header *hdr, uint32_t *stored) {
    uint8_t buf[4];
    if (!stored_counter(stored)) {
        return true;
    }
    if (tlv_find(fap, hdr, IMAGE_TLV_SEC_CNT, true, buf, sizeof(buf)) != 0) {
        return true;
    }
    return get_le32(buf) < *stored;
}
#endif

#if MCUBOOT_POLICY_SINGLE && defined(MCUBOOT_HW_ROLLBACK_PROT)
// bootutil's single slot loader doesn't touch the security counter, and the only slot has no
// confirm step. Instead the image that was validated and is about to start raises the stored
// counter to its own, which is what refuses an older image written to the slot later.
int mcuboot_validate_raise_counter(void) {
    const struct flash_area *pri;
    struct image_header hdr;
    uint8_t buf[4];
    uint32_t stored;
    if (flash_area_open(FLASH_AREA_IMAGE_PRIMARY(0), &pri) != 0 || flash_area_read(pri, 0, &hdr, sizeof(hdr)) != 0
        || hdr.ih_magic != IMAGE_MAGIC || !stored_counter(&stored)) {
        return -EIO;
    }
    if (tlv_find(pri, &hdr, IMAGE_TLV_SEC_CNT, true, buf, sizeof(buf)) != 0) {
        return -ENOENT;
    }
    uint32_t cnt = get_le32(buf);
    return cnt > stored ? boot_nv_security_counter_update(0, cnt) : 0;
}
#endif

#if defined(MCUBOOT_DOWNGRADE_PREVENTION) && !MCUBOOT_POLICY_SINGLE
// True if the image is older than the one in the primary slot (equal versions pass, as in
// loader.c). A primary slot without an image header does not prevent anything.
static bool older_than_primary(const struct image_header *hdr) {
    const struct flash_area *pri;
    struct image_header cur;
    if (flash_area_open(FLASH_AREA_IMAGE_PRIMARY(0), &pri) != 0 || flash_area_read(pri, 0, &cur, sizeof(cur)) != 0
        || cur.ih_magic != IMAGE_MAGIC) {
        return false;
    }
    return boot_compare_version(&hdr->ih_ver, &cur.ih_ver) < 0;
}
#endif

static mcuboot_validate_result_t fail(mcuboot_validate_result_t r, mcuboot_result_t code, uint32_t detail) {
    r.code = (uint16_t)code;
    r.detail = detail;
    return r;
}

// bootutil_img_validate() only says that something is wrong. The hash it computed tells a
// damaged image from a rejected signature: if the SHA-256 TLV matches, the signature, the key or
// the security counter is at fault.
static mcuboot_validate_result_t classify(mcuboot_validate_result_t r, const struct flash_area *fap, const struct image_header *hdr, const uint8_t *digest, uint32_t tlv_end) {
    static const uint8_t zero[IMG_HASH_LEN];
    if (memcmp(digest, zero, sizeof(zero)) == 0) {
        // No hash was computed: the image could not be read (the structure was checked before).
        int64_t bad = first_read_failure(fap, 0, tlv_end);
        return bad >= 0 ? fail(r, MCUBOOT_RES_ERR_FLASH, (uint32_t)bad) : fail(r, MCUBOOT_RES_ERR_HEADER, 0);
    }
    uint8_t tlv_hash[IMG_HASH_LEN];
    if (tlv_find(fap, hdr, IMAGE_TLV_SHA256, false, tlv_hash, IMG_HASH_LEN) != 0 || memcmp(tlv_hash, digest, IMG_HASH_LEN) != 0) {
        return fail(r, MCUBOOT_RES_ERR_HASH, 0);
    }
    #if defined(MCUBOOT_HW_ROLLBACK_PROT)
    uint32_t stored = 0;
    if (counter_rejected(fap, hdr, &stored)) {
        return fail(r, MCUBOOT_RES_ERR_DOWNGRADE, stored);
    }
    #endif
    return fail(r, MCUBOOT_RES_ERR_SIG, 0);
}

mcuboot_validate_result_t mcuboot_validate_view(const struct flash_area *fap, uint32_t flags) {
    mcuboot_validate_result_t r;
    struct image_header hdr;
    memset(&r, 0, sizeof(r));
    fap = update_view(fap);

    #if VALIDATE_NEEDS_STATE
    int rc = state_init();
    if (rc != 0) {
        return fail(r, MCUBOOT_RES_ERR_LAYOUT, (uint32_t)-rc);
    }
    struct boot_loader_state *state = &s_state;
    #else
    struct boot_loader_state *state = NULL;
    #endif

    // Header.
    if (flash_area_read(fap, 0, &hdr, sizeof(hdr)) != 0) {
        return fail(r, MCUBOOT_RES_ERR_FLASH, 0);
    }
    if (hdr.ih_magic != IMAGE_MAGIC || hdr.ih_hdr_size < IMAGE_HEADER_SIZE) {
        return fail(r, MCUBOOT_RES_ERR_HEADER, 0);
    }
    info_fill(fap, &hdr, &r.info);

    // Flags bootutil refuses at boot (boot_check_header_valid()): not bootable, encrypted or
    // compressed. These features are not built.
    if ((hdr.ih_flags & (IMAGE_F_NON_BOOTABLE | IMAGE_F_ENCRYPTED_AES128 | IMAGE_F_ENCRYPTED_AES256 | IMAGE_F_COMPRESSED_LZMA1
                         | IMAGE_F_COMPRESSED_LZMA2 | IMAGE_F_COMPRESSED_ARM_THUMB_FLT)) != 0) {
        return fail(r, MCUBOOT_RES_ERR_NOT_TARGET, hdr.ih_flags);
    }
    if (flags & VALIDATE_CHECK_TARGET) {
        if ((hdr.ih_flags & IMAGE_F_RAM_LOAD) != 0 || hdr.ih_hdr_size != MCUBOOT_HEADER_SIZE) {
            return fail(r, MCUBOOT_RES_ERR_NOT_TARGET, hdr.ih_flags);
        }
    }

    // Sizes: header, image and protected TLVs inside the area (as boot_image_load_header()),
    // then the whole TLV area and the largest image of the layout.
    uint32_t tlv_off;
    uint32_t span;
    if (__builtin_add_overflow(hdr.ih_hdr_size, hdr.ih_img_size, &tlv_off) || __builtin_add_overflow(tlv_off, hdr.ih_protect_tlv_size, &span)) {
        return fail(r, MCUBOOT_RES_ERR_HEADER, 0);
    }
    if (span >= flash_area_get_size(fap)) {
        return fail(r, MCUBOOT_RES_ERR_TOO_BIG, span);
    }
    struct image_tlv_iter it;
    #if defined(MCUBOOT_SWAP_USING_OFFSET)
    it.start_off = 0;
    #endif
    if (bootutil_tlv_iter_begin(&it, &hdr, fap, IMAGE_TLV_ANY, false) != 0) {
        // The TLV info records are missing or inconsistent, or could not be read.
        int64_t bad = first_read_failure(fap, tlv_off, 4);
        if (bad < 0 && hdr.ih_protect_tlv_size != 0) {
            bad = first_read_failure(fap, span, 4);
        }
        return bad >= 0 ? fail(r, MCUBOOT_RES_ERR_FLASH, (uint32_t)bad) : fail(r, MCUBOOT_RES_ERR_HEADER, 0);
    }
    uint32_t tlv_end = it.tlv_end;
    if (tlv_end > flash_area_get_size(fap) || tlv_end > MCUBOOT_MAX_IMAGE_SIZE) {
        return fail(r, MCUBOOT_RES_ERR_TOO_BIG, tlv_end);
    }

    if (flags & VALIDATE_CHECK_TARGET) {
        #if MCUBOOT_MIN_IMAGE_SIZE != 0
        if (tlv_end < MCUBOOT_MIN_IMAGE_SIZE) {
            return fail(r, MCUBOOT_RES_ERR_TOO_SMALL, tlv_end);
        }
        #endif
        uint8_t id[LAYOUT_ID_LEN];
        if (tlv_find(fap, &hdr, LAYOUT_TLV, true, id, sizeof(id)) != 0) {
            return fail(r, MCUBOOT_RES_ERR_LAYOUT, 0);
        }
        // imgtool writes the id as text order bytes (--custom-tlv 0x00A1 0x<id>): big endian.
        if (get_be32(id) != MCUBOOT_LAYOUT_ID) {
            return fail(r, MCUBOOT_RES_ERR_LAYOUT, get_be32(id));
        }
    }

    if (flags & VALIDATE_CHECK_DOWNGRADE) {
        #if defined(MCUBOOT_HW_ROLLBACK_PROT)
        uint32_t stored = 0;
        if (counter_rejected(fap, &hdr, &stored)) {
            return fail(r, MCUBOOT_RES_ERR_DOWNGRADE, stored);
        }
        #elif defined(MCUBOOT_DOWNGRADE_PREVENTION) && !MCUBOOT_POLICY_SINGLE
        if (older_than_primary(&hdr)) {
            return fail(r, MCUBOOT_RES_ERR_DOWNGRADE, 0);
        }
        #endif
    }

    if (flags & VALIDATE_STRUCTURE_ONLY) {
        r.code = MCUBOOT_RES_OK;
        return r;
    }

    // Hash, key lookup, signature and security counter. A hash that could not be computed leaves
    // digest zero.
    uint8_t digest[IMG_HASH_LEN];
    memset(digest, 0, sizeof(digest));
    FIH_DECLARE(fih_rc, FIH_FAILURE);
    FIH_CALL(bootutil_img_validate, fih_rc, state, &hdr, fap, s_tmpbuf, sizeof(s_tmpbuf), NULL, 0, digest);
    if (FIH_NOT_EQ(fih_rc, FIH_SUCCESS)) {
        return classify(r, fap, &hdr, digest, tlv_end);
    }
    r.code = MCUBOOT_RES_OK;
    return r;
}
