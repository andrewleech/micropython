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

#ifndef MICROPY_INCLUDED_SHARED_TINYUSB_MBOOT_MBOOT_USBD_H
#define MICROPY_INCLUDED_SHARED_TINYUSB_MBOOT_MBOOT_USBD_H

// mboot_usbd.h — TinyUSB USB device glue for shared/tinyusb/mboot.
//
// This module wires mboot's DFU 1.1 state machine into the TinyUSB device
// stack.  It implements the standard TinyUSB descriptor callbacks
// (tud_descriptor_device_cb, tud_descriptor_configuration_cb,
// tud_descriptor_string_cb), the existing TinyUSB DFU class callbacks
// (tud_dfu_download_cb, tud_dfu_manifest_cb, tud_dfu_upload_cb,
// tud_dfu_abort_cb, tud_dfu_detach_cb, tud_dfu_get_timeout_cb), the new
// tud_dfu_set_alt_cb hook added to lib/tinyusb in this stage, and the
// vendor request handler tud_vendor_control_xfer_cb for opcode 0x80.
//
// The configuration descriptor is built once at mboot_usbd_init() time from
// the active region table.  The port must call mboot_region_init() and
// mboot_dfu_init() before mboot_usbd_init().
//
// Dependency constraints: this header may be included by port-specific code
// that also includes TinyUSB headers.  It must not include py/, extmod/,
// shared/runtime/, or any port-specific header.  It depends only on
// mboot_api.h (stdbool.h, stddef.h, stdint.h) and is freestanding-safe.

#include <stdbool.h>
#include <stdint.h>

// ---------------------------------------------------------------------------
// DFU interface number
// ---------------------------------------------------------------------------

// DFU_INTERFACE_NUMBER — bInterfaceNumber of the DFU interface in the
// configuration descriptor.  The bootloader exposes exactly one interface.
// Override in board config if needed (unusual).
#ifndef MBOOT_DFU_INTERFACE_NUMBER
#define MBOOT_DFU_INTERFACE_NUMBER (0)
#endif

// ---------------------------------------------------------------------------
// Transfer size
// ---------------------------------------------------------------------------

// MBOOT_USBD_XFER_SIZE — wTransferSize advertised in the DFU functional
// descriptor.  Must match CFG_TUD_DFU_XFER_BUFSIZE in tusb_config.h.
// Defaults to MBOOT_DFU_XFER_SIZE from mboot_dfu.h (2048).
#ifndef MBOOT_USBD_XFER_SIZE
#define MBOOT_USBD_XFER_SIZE (2048)
#endif

// ---------------------------------------------------------------------------
// Descriptor buffer sizing
// ---------------------------------------------------------------------------

// Maximum number of alt settings the static configuration descriptor buffer
// can accommodate.  Each alt adds one 9-byte interface descriptor.
// 9 (config) + MBOOT_MAX_ALT_SETTINGS * 9 (interfaces) + 9 (DFU functional).
#ifndef MBOOT_USBD_MAX_ALT_SETTINGS
#define MBOOT_USBD_MAX_ALT_SETTINGS (16)
#endif

// Total worst-case configuration descriptor length.
#define MBOOT_USBD_CFG_DESC_MAX_LEN \
    (9 + (MBOOT_USBD_MAX_ALT_SETTINGS) * 9 + 9)

// ---------------------------------------------------------------------------
// Lifecycle
// ---------------------------------------------------------------------------

// mboot_usbd_init — build the configuration descriptor and prepare for USB
// enumeration.
//
// Must be called after mboot_region_init() and mboot_dfu_init() and before
// tusb_init().  Assembles the configuration descriptor into a static buffer
// using the current region count from mboot_region_count(), and initialises
// the string descriptor for the serial number by calling
// mboot_port_get_serial_number().
//
// Call context: called once at bootloader startup; not interrupt-safe.
// Return: void.
void mboot_usbd_init(void);

// mboot_usbd_leave_requested — return true once the device should reset.
//
// Polled by the bootloader main loop.  Returns true after a successful
// DNLOAD manifest sequence followed by a USB bus reset, or after the host
// calls the vendor-erase opcode on the final block and sets the leave flag
// through the DFU state machine.
//
// The main loop pattern:
//   for (;;) {
//       tud_task();
//       if (mboot_usbd_leave_requested()) {
//           mboot_port_cleanup(MBOOT_RESET_MODE_BOOTLOADER);
//           mboot_port_leave(MBOOT_RESET_MODE_BOOTLOADER);
//       }
//   }
//
// Call context: called from the main loop; not interrupt-safe.
// Return: true if the bootloader should exit.
bool mboot_usbd_leave_requested(void);

#endif // MICROPY_INCLUDED_SHARED_TINYUSB_MBOOT_MBOOT_USBD_H
