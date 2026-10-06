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

#ifndef MICROPY_INCLUDED_SHARED_MCUBOOT_MCUBOOT_VALIDATE_H
#define MICROPY_INCLUDED_SHARED_MCUBOOT_MCUBOOT_VALIDATE_H

// Image validation outside boot_go(), for the DFU front end, fsload and the main flow.
// Bootloader role only.

#include <stdint.h>

#include "mcuboot_config/mcuboot_config.h"
#include "mcuboot_types.h"

struct flash_area;

typedef struct {
    uint16_t code;              // mcuboot_result_t
    uint32_t detail;            // bootutil rc, or byte offset for flash errors, see below
    mcuboot_image_info_t info;  // version, security counter, hash prefix as far as they were parsed
} mcuboot_validate_result_t;

#define VALIDATE_FULL            (1u << 0)  // hash + signature (always on)
#define VALIDATE_CHECK_TARGET    (1u << 1)  // header flags, header size, size limits, layout id TLV == MCUBOOT_LAYOUT_ID
#define VALIDATE_CHECK_DOWNGRADE (1u << 2)  // compare against the primary slot version or the stored security counter
#define VALIDATE_STRUCTURE_ONLY  (1u << 3)  // stop after the header, TLV structure and the checks above: no hash, no signature

// Validates the image at offset 0 of fap with the real bootutil_img_validate() (hash, signature,
// key lookup, security counter) over a populated boot_loader_state. Nothing is written to flash.
//
// fap is one of:
//   - the registered primary slot;
//   - a view of the update slot: a copy of the secondary struct flash_area with fa_off and
//     fa_size moved past the spare sector (area id MCUBOOT_AREA_ID_VIEW), as returned by
//     mcuboot_dfu_target_view(). The registered secondary slot is accepted too and is read at
//     its update base (swap using offset);
//   - the fsload stream area (fa_device_id MCUBOOT_DEV_STREAM).
// A view or stream area is as large as the slot the image is validated for, not as large as the
// image; bytes past the end of the file read as erased flash.
//
// Result code (mcuboot_result_t) and detail:
//   MCUBOOT_RES_OK
//   ERR_FLASH      a read failed; detail = byte offset in fap
//   ERR_HEADER     bad magic, header or TLV area structure (also an image cut short before its TLVs)
//   ERR_NOT_TARGET header flags (not bootable, encrypted, compressed, ram-load) or header size
//   ERR_TOO_BIG    the image does not fit the area or exceeds MCUBOOT_MAX_IMAGE_SIZE
//   ERR_TOO_SMALL  with VALIDATE_CHECK_TARGET and swap using offset: the image, with its header and TLVs,
//                  is at most one erase unit (MCUBOOT_MIN_IMAGE_SIZE); detail = its size
//   ERR_LAYOUT     layout id TLV missing or different; detail = the id found (0 if absent)
//   ERR_DOWNGRADE  older version than the primary slot, or security counter below the stored
//                  value (or missing); detail = the stored counter in counter mode
//   ERR_HASH       the SHA-256 TLV is missing or differs from the image (not with VALIDATE_STRUCTURE_ONLY)
//   ERR_SIG        no usable signature: missing, unknown key or mismatch
mcuboot_validate_result_t mcuboot_validate_view(const struct flash_area *fap, uint32_t flags);

// Parses version, security counter and the first four bytes of the SHA-256 TLV of the image at
// offset 0 of fap without verifying anything. info->valid is 1 when the header has the image
// magic; the security counter and hash prefix stay 0 when their TLVs cannot be read.
void mcuboot_image_info_read(const struct flash_area *fap, mcuboot_image_info_t *info);

#if MCUBOOT_POLICY_SINGLE && defined(MCUBOOT_HW_ROLLBACK_PROT)
// Single slot policy: raises the stored security counter to the one of the image in the primary
// slot, which boot_go() has just validated (bootutil's single slot loader does not). 0 or a
// negative errno-style value.
int mcuboot_validate_raise_counter(void);
#endif

#endif // MICROPY_INCLUDED_SHARED_MCUBOOT_MCUBOOT_VALIDATE_H
