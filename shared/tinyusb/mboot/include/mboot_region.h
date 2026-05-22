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

#ifndef MICROPY_INCLUDED_SHARED_TINYUSB_MBOOT_MBOOT_REGION_H
#define MICROPY_INCLUDED_SHARED_TINYUSB_MBOOT_MBOOT_REGION_H

// mboot_region.h — region table + alt-setting descriptor generation
//
// This module consumes the port-supplied mboot_port_regions[] table and
// provides:
//
//   1. Alt-setting index generation: adjacent mboot_region_t entries that share
//      the same name field AND are contiguous in address space are folded into a
//      single DFU alt setting whose interface string has a comma-joined geometry
//      list.  Adjacent means regions[i].addr == regions[i-1].addr +
//      regions[i-1].size.  Same-name entries with a gap between them are
//      rejected at init (-EINVAL) to prevent the DFU host from computing wrong
//      addresses across the implicit gap.  Entries with different names always
//      produce separate alt settings.
//
//   2. dfu-util-compatible interface-string descriptor generation: format is
//      "@<name> /<addr_hex>/<count>*<size><unit><perm>[,<count>*<size><unit><perm>]*"
//      where the base address is the lowest address in the folded group.
//      Unit selection: 'M' if sector_size is a multiple of 1 MiB and > 0,
//      'K' if sector_size is a multiple of 1 KiB and > 0, 'B' otherwise.
//      The permission character is derived from the region's flags field using
//      the DfuSe encoding 'a'+'c'+perm_bits (see mboot_region_perm_char in
//      mboot_region.c for the exact mapping).
//
//   3. Per-region dispatch: write, erase, and read operations validated against
//      the active alt setting's address extent and forwarded to mboot_port_flash_*.
//
// Restrictions (Stage 0)
// -----------------------
//   - Read operations are constrained to the active alt setting (same policy as
//     write/erase for symmetry; cross-region read is not supported in Stage 0).
//   - mboot_region_set_active returns -EINVAL on out-of-range alt values and
//     leaves the active alt unchanged.  The DFU SET_INTERFACE handler in Stage 1
//     must STALL the request on a negative return.
//   - USB string descriptor content is limited to 126 UTF-16 code units
//     (total descriptor length <= 254 bytes, fitting in the one-byte length
//     field).  mboot_region_get_alt_string returns -1 when the generated
//     interface string would exceed this limit.  Port region tables that trigger
//     this limit are misconfigured (e.g. excessively long name or too many
//     geometry groups); shorten the region name or reduce MBOOT_MAX_REGIONS_PER_ALT.
//
// This header depends only on mboot_api.h (which itself depends only on
// stdint.h, stddef.h, stdbool.h).  Do not add any port or TinyUSB includes.

#include "mboot_api.h"

// Maximum number of DFU alt settings (== region groups after folding adjacent
// same-name regions).  The USB descriptor builder in mboot_usbd.c sizes its
// configuration-descriptor buffer from MBOOT_USBD_MAX_ALT_SETTINGS; the two
// must satisfy MBOOT_MAX_ALT_SETTINGS <= MBOOT_USBD_MAX_ALT_SETTINGS, asserted
// at compile time in mboot_usbd.c.
#ifndef MBOOT_MAX_ALT_SETTINGS
#define MBOOT_MAX_ALT_SETTINGS (16)
#endif

// ---------------------------------------------------------------------------
// Initialisation
// ---------------------------------------------------------------------------

// mboot_region_init — validate the port's region table and build alt-setting
// index.
//
// Must be called once before any other function in this module.  Walks
// mboot_port_regions[] and:
//   1. Verifies regions are in strictly ascending address order with no gaps
//      within a same-name group (required for correct folding).
//   2. Verifies sector_count * sector_size == size for each region.
//   3. Calls mboot_port_flash_is_writable on each region's full extent.
//   4. Builds the internal alt-setting -> region-group mapping.
//
// Returns 0 on success.
// Returns -EINVAL if:
//   - mboot_port_regions_count is 0
//   - regions are not in ascending address order
//   - two same-name adjacent-by-name but non-adjacent-by-address regions exist
//   - sector_count * sector_size != size for any region
//   - any region fails the mboot_port_flash_is_writable check
//   - the region table exceeds internal capacity limits
int mboot_region_init(void);

// ---------------------------------------------------------------------------
// Alt-setting count and descriptor string
// ---------------------------------------------------------------------------

// mboot_region_count — number of alt settings.
//
// Adjacent contiguous regions with the same name are folded into one alt
// setting.  Returns 0 if mboot_region_init has not been called or found no
// regions.
size_t mboot_region_count(void);

// mboot_region_get_alt_string — write a dfu-util interface-string descriptor.
//
// Produces a USB string descriptor in UTF-16LE encoding:
//   byte 0: total descriptor length in bytes (including these two bytes)
//   byte 1: descriptor type 0x03 (USB string)
//   bytes 2..: UTF-16LE encoded interface string
//
// The interface string format is:
//   "@<name> /<base_addr_hex>/<count>*<size><unit><perm>[,<count>*<size><unit><perm>]*"
//
// where base_addr_hex is the lowest address in the folded group, formatted as
// "0x%08X" (or "0x%016llX" when MBOOT_ADDRESS_SPACE_64BIT=1), and the geometry
// runs are comma-joined segments, one per mboot_region_t in the group.
//
// The permission character follows the dfu-util convention
// 'a' + (readable | erasable<<1 | writable<<2).  ERASE_REQUIRED_BEFORE_WRITE
// is treated as both erasable and writable (the flag is set on any
// region whose backing store is flash):
//   READABLE + ERASE_REQUIRED_BEFORE_WRITE -> 'h' (read+write+erase)
//   ERASE_REQUIRED_BEFORE_WRITE only       -> 'g' (write+erase, no read)
//   READABLE only                          -> 'b' (read-only)
//   neither flag set                       -> 'a' (no access)
//
// The content is limited to 126 UTF-16 code units (total descriptor <= 254
// bytes, fitting the one-byte USB length field).  Returns -1 if alt is out of
// range or the generated content would exceed 126 code units.
//
// out_utf16  Buffer to receive the USB string descriptor (including the two
//            header bytes).  The caller must supply at least
//            MBOOT_REGION_DESC_MAX_BYTES bytes.
// out_max_chars  Maximum number of UTF-16 code units (not bytes) that may be
//                written after the two header bytes.  The descriptor header is
//                not counted in this limit.
//
// Returns the number of UTF-16 code units written (not counting the two header
// bytes), or -1 if alt is out of range or content exceeds the USB string limit.
//
// Note: the two-byte header is always written when alt is in range and the
// content fits within the USB limit.
int mboot_region_get_alt_string(uint8_t alt, uint16_t *out_utf16, size_t out_max_chars);

// Maximum byte length of one alt-setting interface-string descriptor
// (header + content).  Sized for 126 content chars (USB string descriptor
// limit: total length <= 254 bytes = 2 header + 252 content = 126 UTF-16 units),
// plus the 2-byte header.
#define MBOOT_REGION_DESC_MAX_BYTES (2 + 126 * 2)

// Maximum number of content UTF-16 code units in one USB string descriptor.
// A USB string descriptor's total length is one byte; the maximum content is
// (255 - 2) / 2 = 126 code units.
#define MBOOT_REGION_DESC_MAX_CONTENT_CHARS (126)

// ---------------------------------------------------------------------------
// Active alt-setting tracking
// ---------------------------------------------------------------------------

// mboot_region_set_active — record that the host selected alt setting alt.
//
// Called from the DFU SET_INTERFACE callback (wired in Stage 1).  Returns 0
// on success.  Returns -EINVAL if alt is out of range; the active alt setting
// is left unchanged.  The caller (SET_INTERFACE handler) must STALL the USB
// request on a negative return.
int mboot_region_set_active(uint8_t alt);

// mboot_region_get_active — return the currently active alt setting index.
uint8_t mboot_region_get_active(void);

// mboot_region_active_base — return the base address of the active alt setting.
//
// For a multi-region alt setting (folded group) the base is the lowest address
// across all constituent regions.  Used by mboot_dfu.c to convert wBlockNum to
// a flash address: addr = base + wBlockNum * MBOOT_DFU_XFER_SIZE.
mboot_addr_t mboot_region_active_base(void);

// mboot_region_active_size — return the total byte size of the active alt setting.
//
// For a multi-region alt setting (folded group) the size is group_end -
// group_base, i.e. the sum of all constituent region sizes (assuming they are
// contiguous, which is enforced by mboot_region_init).  Returns 0 if
// mboot_region_init has not been called or the active alt is out of range.
// Used by mboot_dfu.c to detect end-of-region during DFU_UPLOAD and transition
// from dfuUPLOAD-IDLE back to dfuIDLE per DFU 1.1 §6.2.
mboot_addr_t mboot_region_active_size(void);

// ---------------------------------------------------------------------------
// Address dispatch
// ---------------------------------------------------------------------------

// mboot_region_for_address — find the region containing addr.
//
// Returns a pointer to the matching mboot_region_t, or NULL if addr is not
// within any region.
const mboot_region_t *mboot_region_for_address(mboot_addr_t addr);

// mboot_region_sector_size_for_addr — return the sector size for the region
// containing addr.
//
// Returns the sector_size field of the region that contains addr, or 0 if addr
// is not within any region in mboot_port_regions[].  Used by mboot_dfu.c to
// size its per-sector touched bitmap without relying on a fixed-granularity
// approximation.
uint32_t mboot_region_sector_size_for_addr(mboot_addr_t addr);

// mboot_region_write — write to the active alt setting's address space.
//
// addr and addr+len must both lie within the extent of the active alt setting's
// region group.  Returns -ERANGE if the range is not fully contained.
// Forwards to mboot_port_flash_write on success.
int mboot_region_write(mboot_addr_t addr, const uint8_t *src, size_t len);

// mboot_region_erase_page — erase the sector containing addr.
//
// addr must lie within the active alt setting's region group.
// Returns -ERANGE if addr is outside the group.
// Forwards to mboot_port_flash_page_erase and relays next_addr.
int mboot_region_erase_page(mboot_addr_t addr, mboot_addr_t *next_addr);

// mboot_region_read — read from the active alt setting's address space.
//
// addr and addr+len must both lie within the active alt setting's region group
// and the region must have MBOOT_REGION_FLAG_READABLE set.
// Returns -ERANGE if the range is not fully contained.
// Returns -EACCES if the region is not readable.
// Forwards to mboot_port_flash_read on success.
int mboot_region_read(mboot_addr_t addr, uint8_t *dst, size_t len);

// ---------------------------------------------------------------------------
// Sector iteration
// ---------------------------------------------------------------------------

// mboot_region_sector_iter — iterate over the sectors of alt setting alt.
//
// cookie must be initialised to 0 by the caller before the first call.  On
// each call that returns true, *out_addr is the sector start address and
// *out_size is the sector size in bytes.  Returns false (and does not modify
// out_addr/out_size) when the alt setting has been fully enumerated.
//
// Sectors are yielded in strictly ascending address order.  The total number
// of sectors yielded equals the sum of sector_count across all constituent
// regions in the alt setting's group.
bool mboot_region_sector_iter(uint8_t alt, uint32_t *cookie,
    mboot_addr_t *out_addr, uint32_t *out_size);

#endif // MICROPY_INCLUDED_SHARED_TINYUSB_MBOOT_MBOOT_REGION_H
