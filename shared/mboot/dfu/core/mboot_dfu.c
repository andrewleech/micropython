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

// mboot_dfu.c - flash dispatch and touched-sector bookkeeping for DFU writes.
//
// TinyUSB's dfu_device.c owns the DFU 1.1 state machine the host sees on the
// wire.  This module does the per-block flash work for TinyUSB's download
// callback:
//
//   - mboot_dfu_on_dnload         erase-if-needed + write of a DNLOAD block.
//   - mboot_dfu_on_vendor_request vendor opcode 0x80 erase dispatch.
//   - mboot_dfu_notify_set_interface  clear the touched-sector bitmap and
//                                 update the active region on SET_INTERFACE
//                                 or DFU_ABORT.
//
// The entry points do not look at DFU state; TinyUSB checks preconditions
// before the callback fires.
//
// Only libc, mboot_api.h, mboot_dfu.h and mboot_region.h are used.  No py/,
// extmod/, shared/runtime/, tusb.h or port headers.

#include <stdint.h>
#include <stddef.h>
#include <stdbool.h>
#include <string.h>
#include <errno.h>

#include "mboot_api.h"
#include "mboot_dfu.h"
#include "mboot_region.h"

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
// One bit per sector, indexed from sector 0 of the active region.  Bit n is
// set once sector n has been erased in the current download session, and all
// bits are cleared by mboot_dfu_notify_set_interface() (SET_INTERFACE and
// DFU_ABORT).
//
// The bitmap is a static BSS array, MBOOT_DFU_MAX_SECTOR_COUNT bits (4096 =
// 512 bytes by default).
// ---------------------------------------------------------------------------

static uint8_t s_touched[MBOOT_DFU_BITMAP_BYTES];

_Static_assert((MBOOT_DFU_WRITE_ALIGN & (MBOOT_DFU_WRITE_ALIGN - 1)) == 0,
    "MBOOT_DFU_WRITE_ALIGN must be a power of two");
_Static_assert(MBOOT_DFU_XFER_SIZE % MBOOT_DFU_WRITE_ALIGN == 0,
    "MBOOT_DFU_XFER_SIZE must be a multiple of MBOOT_DFU_WRITE_ALIGN");

const unsigned mboot_dfu_write_align = MBOOT_DFU_WRITE_ALIGN;

// Alt setting for which mboot_hook_session_begin() has succeeded in the
// current session, or SESSION_NONE.  Reset along with the bitmap.
#define SESSION_NONE (-1)
static int s_session_alt = SESSION_NONE;

// Staging buffer for the current DNLOAD block.  Aligned so the write does not
// depend on the alignment of the caller's data pointer.
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
    s_session_alt = SESSION_NONE;
    return 0;
}

bool mboot_dfu_session_active(void) {
    return s_session_alt != SESSION_NONE;
}

// Run the session-begin hook once per session for a writable alt setting.
// Read-only alt settings never start a session; the region layer refuses
// their erase and write requests without side effects.
static int session_begin(uint8_t alt) {
    if (s_session_alt == (int)alt || mboot_region_alt_is_read_only(alt)) {
        return 0;
    }
    if (mboot_hook_session_begin(alt) != 0) {
        return MBOOT_DFU_ERR_SESSION_BEGIN;
    }
    s_session_alt = (int)alt;
    return 0;
}

// ---------------------------------------------------------------------------
// DFU_DNLOAD (bRequest=1)
// ---------------------------------------------------------------------------

// Flash address for a block number in the active region.  Returns
// MBOOT_ADDR_INVALID (all-ones) if base + offset wraps.  wBlockNum is
// uint16_t and XFER_SIZE is 2048, so wBlockNum * XFER_SIZE cannot overflow a
// 32-bit mboot_addr_t; only the addition to a high base can.
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
// One bit per sector of the active alt setting, numbered by
// mboot_region_sector_index() (ascending address across the regions of the alt
// setting, whatever their sector sizes).  mboot_region_init() rejects an alt
// setting with more sectors than the bitmap holds.  The bitmap is reset by
// mboot_dfu_notify_set_interface(), which handles SET_INTERFACE and DFU_ABORT.
//
// Returns 0 on success, negative on error.
static int ensure_sector_erased(mboot_addr_t addr) {
    uint32_t sector_idx;
    if (!mboot_region_sector_index(mboot_region_get_active(), addr, &sector_idx)) {
        return -EINVAL;
    }

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
        // End-of-transfer marker.  TinyUSB's DFU class driver does the
        // dfuDNLOAD-IDLE -> dfuMANIFEST transition; no flash work here.
        return 0;
    }

    if (wLength > MBOOT_DFU_XFER_SIZE) {
        return -EINVAL;
    }

    mboot_addr_t addr = block_to_addr(wBlockNum);
    if (addr == (mboot_addr_t)-1) {
        return -EINVAL;
    }

    // Refuse a block outside the active alt setting before the session-begin
    // hook can do anything.
    if (addr - mboot_region_active_base() >= mboot_region_active_size()) {
        return -ERANGE;
    }

    int rc = session_begin(mboot_region_get_active());
    if (rc != 0) {
        return rc;
    }

    rc = ensure_sector_erased(addr);
    if (rc != 0) {
        return rc;
    }

    // Pad into the staging buffer to a MBOOT_DFU_WRITE_ALIGN multiple, then
    // write.
    uint16_t padded_len = (uint16_t)((wLength + (MBOOT_DFU_WRITE_ALIGN - 1)) &
        ~(MBOOT_DFU_WRITE_ALIGN - 1));
    memcpy(s_dnload_buf, data, wLength);
    if (padded_len > wLength) {
        memset(s_dnload_buf + wLength, 0xFF, padded_len - wLength);
    }

    return mboot_region_write(addr, s_dnload_buf, padded_len);
}

// ---------------------------------------------------------------------------
// Vendor request 0x80 - MBOOT_VREQ_ERASE
// ---------------------------------------------------------------------------

static int vendor_erase(uint16_t wValue, const uint8_t *data, uint16_t wLength) {
    // Payload must be exactly 8 bytes: <addr:u32 LE> <length:u32 LE>.
    if (wLength != 8) {
        return -EINVAL;
    }

    uint32_t addr = get_le32(data);
    uint32_t length = get_le32(data + 4);

    // wValue selects the alt setting; 0 means the active one.  Reject an
    // out-of-range alt instead of clamping it to the last valid one.
    if (wValue != 0 && (size_t)wValue >= mboot_region_count()) {
        return -EINVAL;
    }
    uint8_t saved_alt = mboot_region_get_active();
    if (wValue != 0) {
        mboot_region_set_active((uint8_t)(wValue & 0xFF));
    }

    int rc = 0;
    uint8_t alt = mboot_region_get_active();

    if (length == 0xFFFFFFFFu) {
        // Mass erase: walk every sector of the selected alt setting, and only
        // that, and mark each in the touched bitmap so later DNLOAD writes do
        // not erase it again.
        rc = session_begin(alt);
        uint32_t cookie = 0;
        mboot_addr_t sector_addr;
        uint32_t sector_size;
        while (rc == 0) {
            // The cookie is the index of the sector about to be yielded.
            uint32_t sector_idx = cookie;
            if (!mboot_region_sector_iter(alt, &cookie, &sector_addr, &sector_size)) {
                break;
            }
            mboot_addr_t next_addr;
            rc = mboot_region_erase_page(sector_addr, &next_addr);
            if (rc != 0) {
                break;
            }
            bitmap_set(sector_idx);
        }
    } else if (length != 0) {
        // Range erase: erase every sector covering [addr, addr+length) and mark
        // each in the touched bitmap.  Each sector is checked against the
        // selected alt setting as it is reached, so a range that starts inside
        // the alt setting and runs past its end erases the sectors inside it
        // and then fails.
        mboot_addr_t region_base = mboot_region_active_base();
        mboot_addr_t cur = (mboot_addr_t)addr;
        mboot_addr_t end = (mboot_addr_t)addr + (mboot_addr_t)length;
        if (end < cur) {
            rc = -EINVAL;
        } else if (cur - region_base >= mboot_region_active_size()) {
            // Starts outside the alt setting: refuse before the session-begin
            // hook can do anything.
            rc = -ERANGE;
        } else {
            rc = session_begin(alt);
        }
        while (rc == 0 && cur < end) {
            mboot_addr_t next_addr;
            rc = mboot_region_erase_page(cur, &next_addr);
            if (rc != 0) {
                break;
            }
            uint32_t sector_idx;
            if (mboot_region_sector_index(alt, cur, &sector_idx)) {
                bitmap_set(sector_idx);
            }
            cur = next_addr;
        }
    }

    // Restore the previous alt if it was changed.  The touched bitmap and the
    // session-begin state belong to the alt setting of the download session,
    // so erasing a different alt setting invalidates them.
    if (wValue != 0) {
        mboot_region_set_active(saved_alt);
        if (alt != saved_alt) {
            bitmap_clear();
            s_session_alt = SESSION_NONE;
        }
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
            // Any other opcode.  0x81 (MBOOT_VREQ_RESULT) is served by
            // mboot_usbd.c and never gets here; 0x82..0x8F are unused.
            return -EINVAL;
    }
}

void mboot_dfu_notify_set_interface(uint8_t alt) {
    bitmap_clear();
    s_session_alt = SESSION_NONE;
    mboot_region_set_active(alt);
}
