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

// mboot_dfu.c — flash dispatch and touched-sector bookkeeping for DFU writes.
//
// TinyUSB's dfu_device.c owns the DFU 1.1 state machine that the host sees on
// the wire (DETACH, GETSTATUS, GETSTATE, CLRSTATUS, ABORT and the transitions
// between dfuIDLE, dfuDNBUSY, dfuMANIFEST, ...).  This module provides the
// per-block flash work that TinyUSB's download callback needs:
//
//   - mboot_dfu_on_dnload         erase-if-needed + write of a DNLOAD block.
//   - mboot_dfu_on_vendor_request vendor opcode 0x80 erase dispatch.
//   - mboot_dfu_notify_set_interface  clear the touched-sector bitmap and
//                                 update the active region on SET_INTERFACE
//                                 or DFU_ABORT.
//
// All entry points are unconditional with respect to DFU state; preconditions
// are enforced by TinyUSB before the callback fires.
//
// Compile constraints: libc + mboot_api.h + mboot_dfu.h + mboot_region.h only.
// No py/, extmod/, shared/runtime/, tusb.h, or port-specific headers.

#include <stdint.h>
#include <stddef.h>
#include <stdbool.h>
#include <string.h>
#include <errno.h>

#include "mboot_api.h"
#include "mboot_dfu.h"
#include "mboot_region.h"

// ---------------------------------------------------------------------------
// Optional pack hook.
//
// When MBOOT_ENABLE_PACKING is non-zero, writes in the DNLOAD path are routed
// through mboot_pack_write() (declared in mboot_pack.h) instead of directly
// to mboot_region_write().  With MBOOT_ENABLE_PACKING=0 (the default)
// this block is not compiled and mboot_pack.h is never included, so the file
// links cleanly against libc + mboot_api.h + mboot_dfu.h + mboot_region.h
// only.
// ---------------------------------------------------------------------------

#if MBOOT_ENABLE_PACKING
#include "mboot_pack.h"
#endif

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

// Read a 32-bit little-endian value from an unaligned byte pointer.
static inline uint32_t get_le32(const uint8_t *p) {
    return (uint32_t)p[0]
           | ((uint32_t)p[1] << 8)
           | ((uint32_t)p[2] << 16)
           | ((uint32_t)p[3] << 24);
}

// ---------------------------------------------------------------------------
// Touched-sector bitmap
//
// One bit per sector, indexed from sector 0 of the active region.
// Bit n is set after sector n has been erased in the current download session.
// Cleared on SET_INTERFACE and DFU_ABORT via mboot_dfu_notify_set_interface().
//
// The bitmap is stored in a statically allocated BSS array.  Maximum sector
// count is bounded by MBOOT_DFU_MAX_SECTOR_COUNT (default 4096 = 512 bytes).
// ---------------------------------------------------------------------------

static uint8_t s_touched[MBOOT_DFU_BITMAP_BYTES];

// Staging buffer for the current DNLOAD block.  File-scope so writes can use
// aligned access regardless of the caller's data pointer alignment.
static uint8_t s_dnload_buf[MBOOT_DFU_XFER_SIZE] __attribute__((aligned(4)));

static void bitmap_clear(void) {
    memset(s_touched, 0, sizeof(s_touched));
}

static bool bitmap_test(uint32_t sector_idx) {
    if (sector_idx >= MBOOT_DFU_MAX_SECTOR_COUNT) {
        return false;
    }
    return (s_touched[sector_idx / 8] & (1u << (sector_idx % 8))) != 0;
}

static void bitmap_set(uint32_t sector_idx) {
    if (sector_idx < MBOOT_DFU_MAX_SECTOR_COUNT) {
        s_touched[sector_idx / 8] |= (1u << (sector_idx % 8));
    }
}

// ---------------------------------------------------------------------------
// Lifecycle
// ---------------------------------------------------------------------------

int mboot_dfu_init(void) {
    bitmap_clear();
    return 0;
}

// ---------------------------------------------------------------------------
// DFU_DNLOAD (bRequest=1)
// ---------------------------------------------------------------------------

// Compute the flash address for a given block number using the active region.
// Returns MBOOT_ADDR_INVALID (all-ones) if the base+offset addition wraps.
// wBlockNum is uint16_t and XFER_SIZE is 2048, so wBlockNum * XFER_SIZE
// cannot itself overflow even a 32-bit mboot_addr_t; only the final addition
// against a high base address needs to be checked.
static mboot_addr_t block_to_addr(uint16_t wBlockNum) {
    mboot_addr_t base = mboot_region_active_base();
    mboot_addr_t offset = (mboot_addr_t)wBlockNum * MBOOT_DFU_XFER_SIZE;
    mboot_addr_t result = base + offset;
    if (result < base) {
        return (mboot_addr_t)-1;
    }
    return result;
}

// Erase the sector containing addr if it has not been touched this session.
//
// Bitmap indexing: uses mboot_region_sector_size_for_addr() to obtain the
// sector size for the region containing addr.  The sector index is computed as
// (sector_start - region_base) / sector_size, where sector_start is
// (addr / sector_size) * sector_size.  This gives exactly one bit per physical
// sector regardless of write order or alignment.  The bitmap is reset on every
// SET_INTERFACE event (and on DFU_ABORT, which routes through the same
// callback) via mboot_dfu_notify_set_interface().
//
// Returns 0 on success, negative on error.
static int ensure_sector_erased(mboot_addr_t addr) {
    mboot_addr_t region_base = mboot_region_active_base();
    if (addr < region_base) {
        return -EINVAL;
    }

    uint32_t sector_size = mboot_region_sector_size_for_addr(addr);
    if (sector_size == 0) {
        return -EINVAL;
    }

    mboot_addr_t sector_start = (addr / sector_size) * sector_size;
    uint32_t sector_idx = (uint32_t)((sector_start - region_base) / sector_size);

    if (bitmap_test(sector_idx)) {
        return 0;
    }

    mboot_addr_t next_addr;
    int rc = mboot_region_erase_page(addr, &next_addr);
    if (rc != 0) {
        return rc;
    }

    bitmap_set(sector_idx);
    return 0;
}

int mboot_dfu_on_dnload(uint16_t wBlockNum, const uint8_t *data, uint16_t wLength) {
    if (wLength == 0) {
        // End-of-transfer marker.  The TinyUSB DFU class driver handles the
        // dfuDNLOAD-IDLE -> dfuMANIFEST transition; no flash work to do here.
        return 0;
    }

    if (wLength > MBOOT_DFU_XFER_SIZE) {
        return -EINVAL;
    }

    mboot_addr_t addr = block_to_addr(wBlockNum);
    if (addr == (mboot_addr_t)-1) {
        return -EINVAL;
    }

    int rc = ensure_sector_erased(addr);
    if (rc != 0) {
        return rc;
    }

    // Pad to a 4-byte multiple into the staging buffer, then write.
    uint16_t padded_len = (wLength + 3u) & ~3u;
    memcpy(s_dnload_buf, data, wLength);
    if (padded_len > wLength) {
        memset(s_dnload_buf + wLength, 0xFF, padded_len - wLength);
    }

    #if MBOOT_ENABLE_PACKING
    rc = mboot_pack_write(addr, s_dnload_buf, padded_len, false);
    #else
    rc = mboot_region_write(addr, s_dnload_buf, padded_len);
    #endif
    return rc;
}

// ---------------------------------------------------------------------------
// Vendor request 0x80 — MBOOT_VREQ_ERASE
// ---------------------------------------------------------------------------

static int vendor_erase(uint16_t wValue, const uint8_t *data, uint16_t wLength) {
    // Payload must be exactly 8 bytes: <addr:u32 LE> <length:u32 LE>.
    if (wLength != 8) {
        return -EINVAL;
    }

    uint32_t addr = get_le32(data);
    uint32_t length = get_le32(data + 4);

    // wValue selects the region (alt setting).  0 means use the active region.
    // Validate against the known region count; reject out-of-range alts rather
    // than silently clamping to the last valid one.
    if (wValue != 0 && (size_t)wValue >= mboot_region_count()) {
        return -EINVAL;
    }
    uint8_t saved_alt = mboot_region_get_active();
    if (wValue != 0) {
        mboot_region_set_active((uint8_t)(wValue & 0xFF));
    }

    int rc = 0;

    if (length == 0xFFFFFFFFu) {
        // Mass-erase: iterate all sectors in the selected region and mark each
        // sector in the touched bitmap so subsequent DNLOAD writes do not erase again.
        uint32_t cookie = 0;
        mboot_addr_t sector_addr;
        uint32_t sector_size;
        uint8_t alt = mboot_region_get_active();
        mboot_addr_t region_base = mboot_region_active_base();
        while (mboot_region_sector_iter(alt, &cookie, &sector_addr, &sector_size)) {
            mboot_addr_t next_addr;
            rc = mboot_region_erase_page(sector_addr, &next_addr);
            if (rc != 0) {
                break;
            }
            if (sector_size > 0) {
                uint32_t sector_idx = (uint32_t)((sector_addr - region_base) / sector_size);
                bitmap_set(sector_idx);
            }
        }
    } else {
        // Range erase: erase all sectors covering [addr, addr+length) and mark
        // each sector in the touched bitmap.
        if (length == 0) {
            rc = 0; // no-op
        } else {
            mboot_addr_t region_base = mboot_region_active_base();
            mboot_addr_t cur = (mboot_addr_t)addr;
            mboot_addr_t end = (mboot_addr_t)addr + (mboot_addr_t)length;
            while (cur < end) {
                mboot_addr_t next_addr;
                rc = mboot_region_erase_page(cur, &next_addr);
                if (rc != 0) {
                    break;
                }
                uint32_t sector_size_cur = mboot_region_sector_size_for_addr(cur);
                if (sector_size_cur > 0) {
                    mboot_addr_t sector_start = (cur / sector_size_cur) * sector_size_cur;
                    uint32_t sector_idx = (uint32_t)((sector_start - region_base) / sector_size_cur);
                    bitmap_set(sector_idx);
                }
                cur = next_addr;
            }
        }
    }

    // Restore previous alt if we changed it.
    if (wValue != 0) {
        mboot_region_set_active(saved_alt);
    }

    return rc;
}

int mboot_dfu_on_vendor_request(uint8_t bRequest, uint16_t wValue, uint16_t wIndex,
    const uint8_t *data, uint16_t wLength) {
    (void)wIndex;

    switch (bRequest) {
        case MBOOT_VREQ_ERASE:
            return vendor_erase(wValue, data, wLength);
        default:
            // Unallocated opcodes 0x81..0x8F.
            return -EINVAL;
    }
}

void mboot_dfu_notify_set_interface(uint8_t alt) {
    bitmap_clear();
    mboot_region_set_active(alt);
}
