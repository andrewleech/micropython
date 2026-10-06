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

// mboot_dfu.h - flash dispatch helper for the TinyUSB DFU class glue.
//
// TinyUSB's dfu_device.c owns the DFU 1.1 state machine.  This module does the
// flash-side work for TinyUSB's download and vendor-request callbacks: erase
// if needed and program each block, dispatch vendor opcode 0x80 erase, and
// keep the touched-sector bitmap that turns out-of-order DNLOAD blocks into a
// single erase per sector per session.
//
// DfuSe is not supported.  DFU_DNLOAD with wBlockNum == 0 is a normal data
// block, not the DfuSe block-0 command channel of ports/stm32/mboot/main.c.
// Use --dfuse in tools/pydfu.py for legacy stm32 mboot devices.
//
// wBlockNum does not roll over (the DFU 1.1 spec leaves that
// implementation-defined).  A transfer larger than
// 65535 * MBOOT_DFU_XFER_SIZE bytes needs multiple alt settings or a larger
// wTransferSize.
//
// Implicit per-sector erase: the module keeps a per-session bitmap of touched
// sectors.  It tracks erase units, not program pages, to bound memory use.  On
// each DFU_DNLOAD block the sector containing the target address is erased
// once per session and its bit is set after the erase.  Vendor request 0x80
// (mass-erase and range-erase) also sets the bits of the sectors it erases, so
// a later DNLOAD to them does not erase again.  The bitmap is cleared on
// SET_INTERFACE and DFU_ABORT, both routed through
// mboot_dfu_notify_set_interface().
//
// Bitmap size: the number of sectors in one alt setting (the sum over the
// regions folded into it) is bounded at compile time by
// MBOOT_DFU_MAX_SECTOR_COUNT (default 4096), and mboot_region_init() rejects a
// region table that exceeds it.  At 1 bit per sector that is 512 bytes of BSS,
// enough for a 16 MiB alt setting of 4 KiB sectors.  Define a larger value for
// more sectors.
//
// All address resolution and flash I/O goes through mboot_region.h.

#include <stdbool.h>
#include <stdint.h>

// ---------------------------------------------------------------------------
// Configuration
// ---------------------------------------------------------------------------

// Transfer size advertised in the DFU functional descriptor.
#ifndef MBOOT_DFU_XFER_SIZE
#define MBOOT_DFU_XFER_SIZE (2048)
#endif

// Maximum number of sectors tracked by the touched-sector bitmap.  Must cover
// the total sector_count of the regions of any one alt setting.
#ifndef MBOOT_DFU_MAX_SECTOR_COUNT
#define MBOOT_DFU_MAX_SECTOR_COUNT (4096)
#endif

// Bitmap size in bytes, rounded up.
#define MBOOT_DFU_BITMAP_BYTES ((MBOOT_DFU_MAX_SECTOR_COUNT + 7) / 8)

// Write unit of the flash behind the regions, in bytes.  Must be a power of
// two that divides MBOOT_DFU_XFER_SIZE.  mboot_dfu_on_dnload() pads every block
// with 0xFF up to a multiple of this value, so mboot_port_flash_write() always
// gets an aligned length.  Region base addresses must be aligned to it too.
#ifndef MBOOT_DFU_WRITE_ALIGN
#define MBOOT_DFU_WRITE_ALIGN (4)
#endif

// Value MBOOT_DFU_WRITE_ALIGN had when mboot_dfu.c was compiled.  A front end
// whose own translation units see the macro (from a generated board header,
// say) compares it with this to catch a build that passed it to only some of
// them.
extern const unsigned mboot_dfu_write_align;

// Return value of mboot_dfu_on_dnload() and mboot_dfu_on_vendor_request() when
// mboot_hook_session_begin() failed.  Distinct from every errno value the
// region layer returns.
#define MBOOT_DFU_ERR_SESSION_BEGIN (-1000)

// ---------------------------------------------------------------------------
// Lifecycle
// ---------------------------------------------------------------------------

// mboot_dfu_init - reset the touched-sector bitmap.
//
// Must be called once before any other mboot_dfu_* function.
// Returns 0 on success.
int mboot_dfu_init(void);

// ---------------------------------------------------------------------------
// DFU class flash dispatch
//
// Each entry point is called from a TinyUSB DFU-class callback that has
// already checked the DFU 1.1 state preconditions.  The return value goes back
// to TinyUSB as the success or error of the operation.
// ---------------------------------------------------------------------------

// mboot_dfu_on_dnload - DFU_DNLOAD (bRequest=1) flash dispatch.
//
// wBlockNum: block index, 0-based; address = region_base + wBlockNum *
//   MBOOT_DFU_XFER_SIZE.
// data: payload from the host; may be NULL if wLength == 0.
// wLength: payload byte count.  0 is the end-of-transfer marker: returns 0
//   with no flash access, and TinyUSB handles the manifest transition itself.
//
// Trailing padding: mboot_port_flash_write needs len to be a multiple of
// MBOOT_DFU_WRITE_ALIGN, so a shorter tail is padded with 0xFF before writing
// (the NOR erased state).  With the default alignment of 4, a host that
// uploads back a 3-byte tail reads 4 bytes, the extra one being 0xFF.
//
// Session begin: before the first block of a session on a writable alt
// setting, mboot_hook_session_begin() is called (see mboot_api.h).  If it
// fails, nothing is erased or written and MBOOT_DFU_ERR_SESSION_BEGIN is
// returned.
//
// Returns 0 on success, negative on error.  On error the caller should report
// DFU_STATUS_ERR_WRITE (or similar) to TinyUSB.
int mboot_dfu_on_dnload(uint16_t wBlockNum, const uint8_t *data, uint16_t wLength);

// ---------------------------------------------------------------------------
// Vendor extension entry point
// ---------------------------------------------------------------------------

// mboot_dfu_on_vendor_request - vendor request (bmRequestType=0x41).
//
// Only MBOOT_VREQ_ERASE (0x80) is handled here.  The caller (mboot_usbd.c)
// serves MBOOT_VREQ_RESULT (0x81) itself and does not pass it on; every other
// opcode, including the unallocated 0x82..0x8F, returns -EINVAL.
//
// bRequest=0x80 (MBOOT_VREQ_ERASE):
//   wValue: alt-setting index of the region to erase (0 = use active region).
//   wIndex: DFU interface number (not validated here).
//   data / wLength: must be 8 bytes, whatever the width of mboot_addr_t:
//     bytes [0..3] addr (LE u32), bytes [4..7] length (LE u32).
//     length == 0xFFFFFFFF: mass-erase the selected region.
//     length != 0xFFFFFFFF: erase sectors covering [addr, addr+length).
//
// The session-begin hook is called before the first erase of a session, as for
// DNLOAD; MBOOT_DFU_ERR_SESSION_BEGIN is returned if it fails.
//
// Returns 0 on success, -EINVAL for a payload that is not 8 bytes, an
// out-of-range alt setting or any other opcode, and the negative flash-backend
// error if an erase fails.  Erasing a read-only region fails with -EACCES.  A
// mass erase covers only the sectors of the selected alt setting.
int mboot_dfu_on_vendor_request(uint8_t bRequest, uint16_t wValue, uint16_t wIndex,
    const uint8_t *data, uint16_t wLength);

// mboot_dfu_session_active - true while a write session is open: from the first
// successful mboot_hook_session_begin() until the next
// mboot_dfu_notify_set_interface() (alt switch or DFU_ABORT) or mboot_dfu_init().
bool mboot_dfu_session_active(void);

// mboot_dfu_notify_set_interface - the host changed the alt setting.
//
// Called by the TinyUSB DFU class SET_INTERFACE callback (or the mock
// transport) when the host selects a different alt setting, and by the DFU
// ABORT callback to drop any partial download state.  Clears the touched-sector
// bitmap, re-arms the session-begin hook and passes the new alt setting to
// mboot_region_set_active().
void mboot_dfu_notify_set_interface(uint8_t alt);

#endif // MICROPY_INCLUDED_SHARED_TINYUSB_MBOOT_MBOOT_DFU_H
