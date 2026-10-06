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

// mboot_region.h - region table and alt-setting descriptor generation
//
// This module takes the region table from mboot_port_get_regions() and
// provides:
//
//   1. Alt-setting indexes.  Adjacent mboot_region_t entries with the same name
//      that are contiguous in address (regions[i].addr == regions[i-1].addr +
//      regions[i-1].size) are folded into one DFU alt setting whose interface
//      string has a comma-joined geometry list.  Same-name entries with a gap
//      between them are rejected at init (-EINVAL), because the DFU host would
//      compute wrong addresses across the gap.  Different names always give
//      separate alt settings.
//
//   2. dfu-util compatible interface strings, in the format
//      "@<name> /<addr_hex>/<count>*<size><unit><perm>[,<count>*<size><unit><perm>]*"
//      where the base address is the lowest address in the folded group.
//      Unit is 'M' if sector_size is a multiple of 1 MiB and > 0, 'K' if it is
//      a multiple of 1 KiB and > 0, 'B' otherwise.  The permission character
//      comes from the region flags using the DfuSe encoding (see
//      region_perm_char in mboot_region.c for the mapping).
//
//   3. Per-region write, erase and read, checked against the active alt
//      setting's address extent and passed on to mboot_port_flash_*.
//
// Restrictions
// ------------
//   - Reads are limited to the active alt setting, the same as write and
//     erase.  A read cannot span alt settings.
//   - mboot_region_set_active returns -EINVAL for an out-of-range alt and
//     leaves the active alt unchanged.  mboot_usbd.c refuses the request
//     (DFU_STATUS_ERR_TARGET) on a negative return.
//   - USB string descriptor content is limited to 126 UTF-16 code units (total
//     descriptor length <= 254 bytes, so it fits the one-byte length field).
//     mboot_region_get_alt_string returns -1 when the generated interface
//     string would exceed that.  A region table that hits the limit has a name
//     that is too long or too many geometry groups; shorten the name or reduce
//     MBOOT_MAX_REGIONS_PER_ALT.
//
// This header depends only on mboot_api.h (stdint.h, stddef.h, stdbool.h).  Do
// not add port or TinyUSB includes.

#include "mboot_api.h"

// Maximum number of DFU alt settings (region groups after folding adjacent
// same-name regions).  mboot_usbd.c sizes its configuration-descriptor buffer
// from MBOOT_USBD_MAX_ALT_SETTINGS, and MBOOT_MAX_ALT_SETTINGS must not exceed
// it; mboot_usbd.c asserts that at compile time.
#ifndef MBOOT_MAX_ALT_SETTINGS
#define MBOOT_MAX_ALT_SETTINGS (16)
#endif

// ---------------------------------------------------------------------------
// Initialisation
// ---------------------------------------------------------------------------

// mboot_region_init - validate the port's region table and build the
// alt-setting index.
//
// Must be called once before any other function in this module.  Walks the
// table from mboot_port_get_regions() and:
//   1. Checks that same-name adjacent regions are contiguous and ascending
//      (needed for folding) and that no region overlaps an earlier one.  Alt
//      settings (groups of different names) can be in any address order.
//   2. Checks sector_count * sector_size == size for each region.
//   3. Calls mboot_port_flash_is_writable on each region's full extent, except
//      for MBOOT_REGION_FLAG_READ_ONLY regions.
//   4. Builds the alt-setting to region-group mapping.
//
// Returns 0 on success.
// Returns -EINVAL if:
//   - the region table is empty or NULL
//   - two regions with the same name are adjacent in the table but not in address
//   - two regions overlap
//   - sector_count * sector_size != size for any region
//   - any region that is not read-only fails the mboot_port_flash_is_writable
//     check
//   - the region table exceeds an internal capacity limit
//   - the regions folded into one alt setting have more than
//     MBOOT_DFU_MAX_SECTOR_COUNT sectors in total (the DFU touched-sector
//     bitmap holds one bit per sector of the alt setting)
int mboot_region_init(void);

// ---------------------------------------------------------------------------
// Alt-setting count and descriptor string
// ---------------------------------------------------------------------------

// mboot_region_count - number of alt settings.
//
// Adjacent contiguous regions with the same name are folded into one alt
// setting.  Returns 0 if mboot_region_init has not been called or found no
// regions.
size_t mboot_region_count(void);

// mboot_region_get_alt_string - write a dfu-util interface-string descriptor.
//
// Produces a USB string descriptor in UTF-16LE:
//   byte 0: total descriptor length in bytes (including these two bytes)
//   byte 1: descriptor type 0x03 (USB string)
//   bytes 2..: UTF-16LE interface string
//
// The interface string format is:
//   "@<name> /<base_addr_hex>/<count>*<size><unit><perm>[,<count>*<size><unit><perm>]*"
//
// base_addr_hex is the lowest address in the folded group, formatted as
// "0x%08X".  The geometry runs are comma-joined, one per mboot_region_t in the
// group.
//
// The permission character follows the DfuSe convention that dfu-util parses
// ('a'..'g' = 1 + (readable | erasable<<1 | writable<<2) - 1).
// ERASE_REQUIRED_BEFORE_WRITE counts as both erasable and writable (it is set
// on any region backed by flash):
//   READABLE + ERASE_REQUIRED_BEFORE_WRITE -> 'g' (read+write+erase)
//   ERASE_REQUIRED_BEFORE_WRITE only       -> 'f' (write+erase, no read)
//   READABLE only                          -> 'a' (read-only)
//   READ_ONLY set                          -> never erasable or writable
//
// Content is limited to 126 UTF-16 code units (total descriptor <= 254 bytes,
// which fits the one-byte USB length field).
//
// out_utf16  Buffer for the USB string descriptor, header bytes included.  The
//            caller must supply at least MBOOT_REGION_DESC_MAX_BYTES bytes.
// out_max_chars  Maximum number of UTF-16 code units (not bytes) written after
//                the two header bytes.  The header is not counted.
//
// Returns the number of UTF-16 code units written (not counting the two header
// bytes), or -1 if alt is out of range or the content exceeds the USB string
// limit.  The two-byte header is written whenever alt is in range and the
// content fits.
int mboot_region_get_alt_string(uint8_t alt, uint16_t *out_utf16, size_t out_max_chars);

// Maximum byte length of one alt-setting interface-string descriptor: 126
// content UTF-16 units (the USB string descriptor limit) plus the 2-byte
// header.
#define MBOOT_REGION_DESC_MAX_BYTES (2 + 126 * 2)

// Maximum number of content UTF-16 code units in one USB string descriptor.
// The total length is one byte, so the maximum content is (255 - 2) / 2 = 126
// code units.
#define MBOOT_REGION_DESC_MAX_CONTENT_CHARS (126)

// ---------------------------------------------------------------------------
// Active alt-setting tracking
// ---------------------------------------------------------------------------

// mboot_region_set_active - record that the host selected alt setting alt.
//
// Called by mboot_usbd.c when a DFU callback carries an alt setting other than
// the last one seen (TinyUSB does not report SET_INTERFACE itself), and by
// mboot_dfu.c.  Returns 0 on success, -EINVAL if alt is out of range; the
// active alt setting is then unchanged and the caller refuses the request.
int mboot_region_set_active(uint8_t alt);

// mboot_region_get_active - return the currently active alt setting index.
uint8_t mboot_region_get_active(void);

// mboot_region_active_base - return the base address of the active alt setting.
//
// For a multi-region alt setting (folded group) this is the lowest address of
// its regions.  mboot_dfu.c uses it to convert wBlockNum to a flash address:
// addr = base + wBlockNum * MBOOT_DFU_XFER_SIZE.
mboot_addr_t mboot_region_active_base(void);

// mboot_region_active_size - return the total byte size of the active alt setting.
//
// For a multi-region alt setting (folded group) this is group_end -
// group_base, the sum of the region sizes (mboot_region_init enforces that
// they are contiguous).  Returns 0 if mboot_region_init has not been called or
// the active alt is out of range.  mboot_dfu.c uses it to find the end of the
// alt setting for DNLOAD and erase, and mboot_usbd.c to end an upload there.
mboot_addr_t mboot_region_active_size(void);

// mboot_region_sector_index - index of the sector containing addr within alt
// setting alt.
//
// Sectors of the alt setting are numbered from 0 in ascending address order
// across its regions, which may have different sector sizes (the numbering of
// mboot_region_sector_iter).  Returns true and stores the index in *out_idx if
// addr lies within the alt setting; returns false and leaves *out_idx
// unchanged if alt is out of range or addr is outside it.  mboot_dfu.c uses the
// index as the position in its touched-sector bitmap.
bool mboot_region_sector_index(uint8_t alt, mboot_addr_t addr, uint32_t *out_idx);

// mboot_region_alt_is_read_only - true if every region of alt setting alt is
// MBOOT_REGION_FLAG_READ_ONLY.  False for an out-of-range alt.
bool mboot_region_alt_is_read_only(uint8_t alt);

// mboot_region_write - write to the active alt setting's address space.
//
// addr and addr+len must both lie within the extent of the active alt setting's
// region group.  Returns -ERANGE if the range is not fully contained and
// -EACCES if it overlaps a MBOOT_REGION_FLAG_READ_ONLY region.  Otherwise
// calls mboot_port_flash_write.
int mboot_region_write(mboot_addr_t addr, const uint8_t *src, size_t len);

// mboot_region_erase_page - erase the sector containing addr.
//
// addr must lie within the active alt setting's region group.  Returns -ERANGE
// if it does not and -EACCES if its region is MBOOT_REGION_FLAG_READ_ONLY.
// Otherwise calls mboot_port_flash_page_erase and passes next_addr through.
int mboot_region_erase_page(mboot_addr_t addr, mboot_addr_t *next_addr);

// mboot_region_read - read from the active alt setting's address space.
//
// addr and addr+len must both lie within the active alt setting's region group
// and the region must have MBOOT_REGION_FLAG_READABLE set.  Returns -ERANGE if
// the range is not fully contained and -EACCES if the region is not readable.
// Otherwise calls mboot_port_flash_read.
int mboot_region_read(mboot_addr_t addr, uint8_t *dst, size_t len);

// ---------------------------------------------------------------------------
// Sector iteration
// ---------------------------------------------------------------------------

// mboot_region_sector_iter - iterate over the sectors of alt setting alt.
//
// cookie must be 0 before the first call.  Each call that returns true sets
// *out_addr to the sector start address and *out_size to the sector size in
// bytes.  Returns false, leaving out_addr and out_size alone, when the alt
// setting has been fully enumerated.
//
// Sectors come in strictly ascending address order, and the total equals the
// sum of sector_count over the regions in the alt setting's group.
bool mboot_region_sector_iter(uint8_t alt, uint32_t *cookie,
    mboot_addr_t *out_addr, uint32_t *out_size);

#endif // MICROPY_INCLUDED_SHARED_TINYUSB_MBOOT_MBOOT_REGION_H
