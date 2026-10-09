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

#ifndef MICROPY_INCLUDED_SHARED_MBOOT_DFU_MBOOT_API_H
#define MICROPY_INCLUDED_SHARED_MBOOT_DFU_MBOOT_API_H

// mboot_api.h - port-binding contract for shared/mboot/dfu
//
// The only interface between the port-agnostic common code
// (shared/mboot/dfu/core/) and the code that binds it to a flash and a
// USB device (shared/mboot/src/dfu_glue.c in this tree).  Common code must
// not include py/, extmod/, shared/runtime/, tusb.h or any port header.  This
// file needs only stdint.h, stddef.h and stdbool.h, so it compiles with a
// libc-only include path and -ffreestanding.
//
// Names and DFU behaviour follow the stm32 bootloader in ports/stm32/mboot/,
// but nothing here depends on its implementation.
//
// Only common/mboot_usbd.c includes the TinyUSB headers, so the lib/tinyusb
// submodule is not needed to compile this header or mboot_dfu.c,
// mboot_region.c and mboot_elem.c.

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

// ---------------------------------------------------------------------------
// Address type
// ---------------------------------------------------------------------------

// mboot_addr_t is the address type used for all flash operations.
typedef uint32_t mboot_addr_t;

// ---------------------------------------------------------------------------
// Region descriptor
// ---------------------------------------------------------------------------

// Flags for mboot_region_t.flags.
#define MBOOT_REGION_FLAG_READABLE                  (0x01u)
#define MBOOT_REGION_FLAG_ERASE_REQUIRED_BEFORE_WRITE (0x02u)

// MBOOT_REGION_FLAG_READ_ONLY - the region can be read (with
// MBOOT_REGION_FLAG_READABLE) but is never written or erased through DFU.
// mboot_region_init() skips mboot_port_flash_is_writable() for it,
// mboot_region_write() and mboot_region_erase_page() return -EACCES, and the
// interface string never grants erase or write permission (a readable region
// is 'a').
#define MBOOT_REGION_FLAG_READ_ONLY                 (0x04u)

// mboot_region_t - one contiguous, programmable memory region.
//
// Regions must not overlap.  A device with non-uniform sector geometry (e.g.
// STM32H7 with 4x16K + 1x64K + 7x128K) is described as a few adjacent
// regions, one per uniform run.  mboot_region.c builds the dfu-util interface
// string for the combined memory unit as a comma-joined list.  Each region's
// sector_count * sector_size must equal size.
//
// Fields
//   addr         Start address of the region.
//   size         Total size in bytes.
//   sector_size  Erase granularity in bytes, a power of two.  dfu-util reads
//                it from the interface string, e.g. "8*004Kg" is 8 sectors
//                of 4 KiB.
//   sector_count Number of erasable sectors (size / sector_size).  Stored so
//                the interface-string generator does not have to divide.
//   name         DFU interface string prefix, e.g. "@Internal Flash" or
//                "@QSPI Flash".  Must be a string literal or otherwise stay
//                valid for the lifetime of the bootloader.
//   flags        Bitmask of MBOOT_REGION_FLAG_* values.
typedef struct {
    mboot_addr_t addr;
    mboot_addr_t size;
    uint32_t sector_size;
    uint32_t sector_count;
    const char *name;
    uint8_t flags;
} mboot_region_t;

// mboot_port_get_regions - return the port's region table.
//
// Called once from mboot_region_init().  The returned pointer and count must
// stay valid for the lifetime of the bootloader, so usually a static const
// array.
//
//   regions_out  Receives the region table (array of mboot_region_t).  Regions
//                of one alt setting are contiguous and ascending; alt settings
//                can be in any address order.
//   count_out    Receives the number of entries in the table.
//
// Typical definition:
//
//   static const mboot_region_t k_regions[] = { ... };
//   void mboot_port_get_regions(const mboot_region_t **r, size_t *c) {
//       *r = k_regions;
//       *c = sizeof(k_regions) / sizeof(k_regions[0]);
//   }
extern void mboot_port_get_regions(const mboot_region_t **regions_out, size_t *count_out);

// ---------------------------------------------------------------------------
// Vendor request opcode reservation
// ---------------------------------------------------------------------------

// DFU vendor requests use bmRequestType = 0x41 (host-to-device, vendor, interface).
// Opcodes 0x80..0x8F are reserved for mboot.
//
//   0x80  MBOOT_VREQ_ERASE - vendor erase request.
//           Payload: always 8 bytes, <addr:4 bytes LE> <length:4 bytes LE>,
//           whatever the width of mboot_addr_t.
//           If length == 0xFFFFFFFF, mass-erase the alt setting selected by
//           wValue (0 = the active one).
//           Otherwise erase the sectors that contain the half-open range
//           [addr, addr+length).  The common code walks the sectors from the
//           one containing addr to the end of the range, each through
//           mboot_port_flash_page_erase.
//   0x81  MBOOT_VREQ_RESULT - vendor result request, device to host
//           (bmRequestType = 0xC1, wLength = 16).  The 16 bytes come from
//           mboot_hook_get_result().
//   0x82..0x8F  Unused.
//
// The DfuSe block-0 commands (0x21 set-address, 0x41 erase-page, carried in
// DFU_DNLOAD payloads, see ports/stm32/mboot/main.c) are not supported.  Use
// --dfuse in tools/pydfu.py for legacy stm32/mboot devices.

#define MBOOT_VREQ_ERASE (0x80u)
#define MBOOT_VREQ_RESULT (0x81u)

// Payload size of MBOOT_VREQ_RESULT.
#define MBOOT_VREQ_RESULT_LEN (16u)

// ---------------------------------------------------------------------------
// Hooks
// ---------------------------------------------------------------------------
//
// The common code calls these three functions and has no default for any of
// them, so a build that does not define all three fails to link.  That way a
// missing hook cannot silently leave a download path that accepts every image.

// mboot_hook_session_begin - a download session starts writing alt setting
// alt.
//
// Called by mboot_dfu.c before the first DNLOAD block or vendor erase after
// mboot_dfu_notify_set_interface(), for an alt setting that is not read-only.
// A non-zero return aborts that request: the DNLOAD callback reports
// DFU_STATUS_ERR_ERASE and the vendor erase request is stalled.  The hook is
// called again on the next request until it returns 0.
extern int mboot_hook_session_begin(uint8_t alt);

// mboot_hook_manifest - the host finished a download of alt setting alt.
//
// Called from tud_dfu_manifest_cb() before the download is accepted.  The
// return value is a DFU 1.1 status code (0 is DFU_STATUS_OK), passed on to
// tud_dfu_finish_flashing().  The leave request is raised only if it is 0.
extern uint8_t mboot_hook_manifest(uint8_t alt);

// mboot_hook_get_result - fill the reply of MBOOT_VREQ_RESULT.
//
// buf has room for len bytes (MBOOT_VREQ_RESULT_LEN).  Returns the number of
// bytes written; anything other than MBOOT_VREQ_RESULT_LEN stalls the request.
extern uint16_t mboot_hook_get_result(uint8_t *buf, uint16_t len);

// ---------------------------------------------------------------------------
// Flash backend callbacks
// ---------------------------------------------------------------------------
//
// Any non-zero return is a failure and is reported to the host
// (DFU_STATUS_ERR_WRITE for a download block, a stalled request for a vendor
// erase); the value itself is not interpreted.  A port whose HAL cannot report
// errors may return 0 unconditionally, but then a failed erase or write looks
// the same as a good one.

// mboot_port_flash_is_writable - region-level allow/deny.
//
// Called by mboot_region_init() once for every region that is not
// MBOOT_REGION_FLAG_READ_ONLY, with the full extent of the region.  The port
// must reject any range that overlaps bootloader flash.
//
// Arguments
//   addr   Start of the range to check.
//   len    Byte length of the range.
//
// Call context: not interrupt-safe.
// Return: true if the range is entirely within a writable area, false
// otherwise.  mboot_region_init() fails with -EINVAL when it returns false.
extern bool mboot_port_flash_is_writable(mboot_addr_t addr, size_t len);

// mboot_port_flash_page_erase - erase one sector.
//
// Erases the sector that contains addr.  addr need not be sector aligned; the
// port rounds down to the sector boundary.  On return *next_addr is the
// address of the first byte past the erased sector (sector start +
// sector_size), so a caller can walk a range by passing *next_addr back in.
//
// Precondition: addr is within a region that mboot_port_flash_is_writable
// accepted.
//
// Arguments
//   addr         Address within the sector to erase.
//   next_addr    Out-pointer for the start address of the next sector.  Must
//                not be NULL.
//
// Call context: not interrupt-safe; blocks for the duration of the erase
// (typically 10-100 ms per sector for NOR flash).
// Return: 0 on success, negative errno-style value on error (e.g. -ETIMEDOUT,
// -EIO).
extern int mboot_port_flash_page_erase(mboot_addr_t addr, mboot_addr_t *next_addr);

// mboot_port_flash_write - program previously erased flash.
//
// Writes len bytes from src to flash starting at addr.  The sectors must
// already be erased: mboot_dfu.c erases each sector (mboot_region_erase_page,
// which calls mboot_port_flash_page_erase) before the first write that touches
// it in a download session.
//
// Alignment:
//   - addr is a multiple of MBOOT_DFU_WRITE_ALIGN (default 4, see mboot_dfu.h).
//   - len is a multiple of MBOOT_DFU_WRITE_ALIGN.
//   - The range lies within a single erased sector, or the port handles
//     cross-sector writes itself.
//   Partial-unit writes are not supported; mboot_dfu.c pads the trailing bytes
//   of a download block with 0xFF before calling this function.
//
// A flash controller whose program granularity is larger than
// MBOOT_DFU_WRITE_ALIGN (a 256-byte page program, for example) must align or
// split the write itself, using a RAM staging buffer if it needs one.  The
// common code only guarantees MBOOT_DFU_WRITE_ALIGN alignment.
//
// Arguments
//   addr   Destination start address (MBOOT_DFU_WRITE_ALIGN aligned).
//   src    Source buffer in RAM, must not alias flash.  For a download block it
//          is the 4-byte aligned staging buffer of mboot_dfu.c.
//   len    Number of bytes to write (multiple of MBOOT_DFU_WRITE_ALIGN).
//
// Call context: not interrupt-safe; may block.
// Return: 0 on success, negative errno-style value on error.
extern int mboot_port_flash_write(mboot_addr_t addr, const uint8_t *src, size_t len);

// mboot_port_flash_read - read from flash (used for DFU_UPLOAD).
//
// Reads len bytes from flash at addr into dst.  The region must be readable
// (MBOOT_REGION_FLAG_READABLE).  If the flash controller caches or buffers
// reads, the port must invalidate that state after a program or erase, because
// DFU_UPLOAD can read back what a download has just written.
//
// Arguments
//   addr   Source start address.
//   dst    Destination buffer in RAM.
//   len    Number of bytes to read.
//
// Call context: not interrupt-safe; may use memcpy on XIP-mapped flash.
// Return: 0 on success, negative errno-style value on error.
extern int mboot_port_flash_read(mboot_addr_t addr, uint8_t *dst, size_t len);

// ---------------------------------------------------------------------------
// USB identity callbacks
// ---------------------------------------------------------------------------

// mboot_port_get_serial_number - write the USB serial number string.
//
// The port writes a NUL-terminated ASCII string of up to (buf_len - 1)
// characters into buf.  buf_len includes the NUL, so a buf_len of 33 fits a
// 32-character hexadecimal serial number.
//
// This differs from mp_usbd_port_get_serial_number(char *buf)
// (shared/tinyusb/mp_usbd.h:85), which takes its bound from
// MICROPY_HW_USB_DESC_STR_MAX and so needs the MicroPython headers.  Here the
// caller passes buf_len, so common code can use a local buffer without py/.
//
// Arguments
//   buf      Buffer to receive the NUL-terminated serial string.
//   buf_len  Size of buf in bytes, including the NUL terminator.
//
// Call context: called once during USB descriptor initialisation; not
// interrupt-safe.
// Return: void.
extern void mboot_port_get_serial_number(char *buf, size_t buf_len);

// mboot_port_get_product_string - return the USB product string.
//
// Returns a NUL-terminated ASCII string for the USB product descriptor.  It
// must stay valid for the lifetime of the bootloader (string literal or static
// storage).
//
// Call context: called once during USB descriptor initialisation.
// Return: non-NULL pointer to a NUL-terminated string.
extern const char *mboot_port_get_product_string(void);

// mboot_port_get_vid - return the USB Vendor ID.
//
// Call context: called once during USB descriptor initialisation.
// Return: 16-bit USB VID.
extern uint16_t mboot_port_get_vid(void);

// mboot_port_get_pid - return the USB Product ID.
//
// Call context: called once during USB descriptor initialisation.
// Return: 16-bit USB PID.
extern uint16_t mboot_port_get_pid(void);

#endif // MICROPY_INCLUDED_SHARED_MBOOT_DFU_MBOOT_API_H
