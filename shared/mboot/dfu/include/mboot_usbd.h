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

#ifndef MICROPY_INCLUDED_SHARED_MBOOT_DFU_MBOOT_USBD_H
#define MICROPY_INCLUDED_SHARED_MBOOT_DFU_MBOOT_USBD_H

// mboot_usbd.h - TinyUSB USB device glue for shared/mboot/dfu.
//
// Connects mboot's DFU 1.1 flash work to the TinyUSB device stack.  It
// implements the TinyUSB descriptor callbacks (tud_descriptor_device_cb,
// tud_descriptor_configuration_cb, tud_descriptor_string_cb), the DFU class
// callbacks (tud_dfu_download_cb, tud_dfu_manifest_cb, tud_dfu_upload_cb,
// tud_dfu_abort_cb, tud_dfu_detach_cb, tud_dfu_get_timeout_cb) and the vendor
// request handler tud_vendor_control_xfer_cb for opcodes 0x80 and 0x81.
//
// The configuration descriptor is built once in mboot_usbd_init() from the
// region table.  The port must call mboot_region_init() and mboot_dfu_init()
// first.
//
// Port code that includes TinyUSB headers can include this header.  It
// includes no py/, extmod/, shared/runtime/ or port header, depends only on
// mboot_api.h (stdbool.h, stddef.h, stdint.h) and is freestanding-safe.

#include <stdbool.h>
#include <stdint.h>

// ---------------------------------------------------------------------------
// DFU interface number
// ---------------------------------------------------------------------------

// MBOOT_DFU_INTERFACE_NUMBER - bInterfaceNumber of the DFU interface in the
// configuration descriptor.  The bootloader exposes exactly one interface.
// A board config can override it.
#ifndef MBOOT_DFU_INTERFACE_NUMBER
#define MBOOT_DFU_INTERFACE_NUMBER (0)
#endif

// ---------------------------------------------------------------------------
// Transfer size
// ---------------------------------------------------------------------------

// MBOOT_USBD_XFER_SIZE - wTransferSize advertised in the DFU functional
// descriptor.  Must match CFG_TUD_DFU_XFER_BUFSIZE in tusb_config.h and
// MBOOT_DFU_XFER_SIZE from mboot_dfu.h (2048).
#ifndef MBOOT_USBD_XFER_SIZE
#define MBOOT_USBD_XFER_SIZE (2048)
#endif

// ---------------------------------------------------------------------------
// Descriptor buffer sizing
// ---------------------------------------------------------------------------

// Maximum number of alt settings the static configuration descriptor buffer
// holds.  Each alt adds one 9-byte interface descriptor:
// 9 (config) + MBOOT_USBD_MAX_ALT_SETTINGS * 9 (interfaces) + 9 (DFU functional).
#ifndef MBOOT_USBD_MAX_ALT_SETTINGS
#define MBOOT_USBD_MAX_ALT_SETTINGS (16)
#endif

// Total worst-case configuration descriptor length.
#define MBOOT_USBD_CFG_DESC_MAX_LEN \
    (9 + (MBOOT_USBD_MAX_ALT_SETTINGS) * 9 + 9)

// ---------------------------------------------------------------------------
// Lifecycle
// ---------------------------------------------------------------------------

// mboot_usbd_init - build the configuration descriptor and prepare for USB
// enumeration.
//
// Must be called after mboot_region_init() and mboot_dfu_init() and before
// tusb_init().  Assembles the configuration descriptor into a static buffer
// using mboot_region_count(), and sets up the serial number string descriptor
// by calling mboot_port_get_serial_number().
//
// Call context: called once at bootloader startup; not interrupt-safe.
// Return: void.
void mboot_usbd_init(void);

// mboot_usbd_leave_requested - return true once the device should reset.
//
// Polled once per iteration by the bootloader main loop.  The call also
// watches for a USB bus reset or disconnect and ends the download session of
// that connection.  TinyUSB has no callback for that; it drops the
// configuration, so the configured state going away is the signal.  Returns
// true after a successful DNLOAD manifest sequence followed by such a bus
// reset.
//
// Call context: called from the main loop; not interrupt-safe.
// Return: true if the bootloader should exit.
bool mboot_usbd_leave_requested(void);

// mboot_usbd_bus_reset - the bus was reset or the host went away.  Called by
// mboot_usbd_leave_requested() when it sees the configuration go away, and
// exposed so host tests can drive it.  Ends the download session and, after a
// successful manifest, latches the leave request.
void mboot_usbd_bus_reset(void);

// mboot_usbd_activity - number of DFU download, upload, manifest and abort
// callbacks and vendor control requests seen so far (wraps at 2^32).  A caller
// that polls it can tell whether the host is still talking to the device.
//
// Call context: called from the main loop; not interrupt-safe.
uint32_t mboot_usbd_activity(void);

#endif // MICROPY_INCLUDED_SHARED_MBOOT_DFU_MBOOT_USBD_H
