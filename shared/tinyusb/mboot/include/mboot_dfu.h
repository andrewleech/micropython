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

#ifndef MICROPY_INCLUDED_SHARED_TINYUSB_MBOOT_MBOOT_DFU_H
#define MICROPY_INCLUDED_SHARED_TINYUSB_MBOOT_MBOOT_DFU_H

// mboot_dfu.h — flash dispatch helper for the TinyUSB DFU class glue.
//
// TinyUSB's dfu_device.c owns the DFU 1.1 state machine.  This module provides
// the flash-side work that TinyUSB's download and vendor-request callbacks
// need: per-block erase-if-needed + program, vendor opcode 0x80 erase
// dispatch, and the touched-sector bitmap that turns out-of-order DNLOAD
// blocks into a single erase per sector per session.
//
// DfuSe is intentionally not supported.  DFU_DNLOAD with wBlockNum == 0 is
// treated as a normal data block; the DfuSe block-0 command channel
// (ports/stm32/mboot/main.c:947-965) is the legacy behaviour this module
// replaces.  Use --dfuse in tools/pydfu.py to communicate with legacy stm32
// mboot devices.
//
// wBlockNum rollover: the DFU 1.1 spec leaves behaviour above wBlockNum=65535
// implementation-defined.  This module does not roll over.  For transfers
// exceeding 65535 * MBOOT_DFU_XFER_SIZE bytes the host must use multiple
// alt-settings or a larger wTransferSize.
//
// Implicit per-sector erase: the module maintains a per-session bitmap of
// touched sectors.  The bitmap tracks erase units (sectors), not program
// pages, to bound memory usage.  On each DFU_DNLOAD block, the sector
// containing the target address is erased exactly once per session; the
// corresponding bitmap bit is set after the erase.  Vendor request 0x80
// (mass-erase and range-erase paths) also marks erased sectors in the bitmap
// so that a subsequent DNLOAD to those sectors does not issue a redundant
// erase call.  The bitmap is cleared on SET_INTERFACE and DFU_ABORT, both
// routed through mboot_dfu_notify_set_interface().
//
// Bitmap sizing: the maximum sector count across all regions is bounded at
// compile time by MBOOT_DFU_MAX_SECTOR_COUNT (default 4096).  At 1 bit/sector
// that is 512 bytes in BSS.  For a 16 MiB region at 4 KiB sectors this is
// exact.  Ports with more sectors must define a larger value.
//
// Region dispatch: this module calls into mboot_region.h for all address
// resolution and flash I/O.

#include <stdbool.h>
#include <stdint.h>

// ---------------------------------------------------------------------------
// Configuration
// ---------------------------------------------------------------------------

// Transfer size advertised in the DFU functional descriptor.
#ifndef MBOOT_DFU_XFER_SIZE
#define MBOOT_DFU_XFER_SIZE (2048)
#endif

// Maximum number of sectors tracked by the touched-page bitmap.
// Must cover the largest sector_count of any region in mboot_port_regions[].
#ifndef MBOOT_DFU_MAX_SECTOR_COUNT
#define MBOOT_DFU_MAX_SECTOR_COUNT (4096)
#endif

// Bitmap size in bytes, rounded up.
#define MBOOT_DFU_BITMAP_BYTES ((MBOOT_DFU_MAX_SECTOR_COUNT + 7) / 8)

// ---------------------------------------------------------------------------
// Lifecycle
// ---------------------------------------------------------------------------

// mboot_dfu_init — reset the touched-sector bitmap.
//
// Must be called once before any other mboot_dfu_* function.
// Returns 0 on success.
int mboot_dfu_init(void);

// ---------------------------------------------------------------------------
// DFU class flash dispatch
//
// Each entry point is called from a TinyUSB DFU-class callback that has
// already enforced the DFU 1.1 state preconditions.  Return values are
// passed back to TinyUSB as the wire-level success/error of the operation.
// ---------------------------------------------------------------------------

// mboot_dfu_on_dnload — DFU_DNLOAD (bRequest=1) flash dispatch.
//
// wBlockNum: block index (0-based; address = region_base + wBlockNum *
//   MBOOT_DFU_XFER_SIZE).
// data: payload bytes from the host (host-to-device); may be NULL if
//   wLength == 0.
// wLength: payload byte count.  wLength == 0 is the end-of-transfer marker
//   and returns 0 with no flash side effect; TinyUSB handles the manifest
//   transition itself.
//
// Trailing padding: mboot_port_flash_write requires len to be a multiple of 4.
// If wLength is not a multiple of 4, this function pads the trailing bytes to
// 0xFF before writing.  A host that uploads back a 3-byte tail will read 4
// bytes (the extra byte being 0xFF).  This matches NOR flash erased state and
// is the contractually correct behaviour per mboot_api.h.
//
// Returns 0 on success, negative on error.  On error the caller should
// surface DFU_STATUS_ERR_WRITE (or similar) to TinyUSB.
int mboot_dfu_on_dnload(uint16_t wBlockNum, const uint8_t *data, uint16_t wLength);

// ---------------------------------------------------------------------------
// Vendor extension entry point
// ---------------------------------------------------------------------------

// mboot_dfu_on_vendor_request — vendor class request (bRequestType=0x41).
//
// Handles opcodes in the range 0x80..0x8F.  Opcodes outside this range must
// not be passed to this function; the caller (TinyUSB DFU class glue) is
// responsible for filtering.
//
// bRequest=0x80 (MBOOT_VREQ_ERASE):
//   wValue: alt-setting index of the region to erase (0 = use active region).
//   wIndex: DFU interface number (informational; not validated here).
//   data / wLength: must be 8 bytes on a 32-bit build (or 16 on 64-bit):
//     bytes [0..3] addr (LE u32), bytes [4..7] length (LE u32).
//     length == 0xFFFFFFFF: mass-erase the selected region.
//     length != 0xFFFFFFFF: erase sectors covering [addr, addr+length).
//
// Returns 0 on success, -EINVAL for unrecognised opcodes 0x81..0x8F, negative
// flash-backend error on erase failure.
int mboot_dfu_on_vendor_request(uint8_t bRequest, uint16_t wValue, uint16_t wIndex,
    const uint8_t *data, uint16_t wLength);

// mboot_dfu_notify_set_interface — notify that the host changed the alt setting.
//
// Called by the TinyUSB DFU class SET_INTERFACE callback (or the mock
// transport) when the host selects a different alt setting, and by the DFU
// ABORT callback to drop any partial download state.  Clears the touched-
// sector bitmap and forwards the new alt setting to mboot_region_set_active().
void mboot_dfu_notify_set_interface(uint8_t alt);

#endif // MICROPY_INCLUDED_SHARED_TINYUSB_MBOOT_MBOOT_DFU_H
