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

// mboot_usbd.c - TinyUSB USB device glue for shared/mboot/dfu.
//
// Uses libc, mboot_api.h, mboot_usbd.h, mboot_dfu.h, mboot_region.h and the
// TinyUSB src/ headers.  No py/, extmod/, shared/runtime/ or port headers.

#include <errno.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <string.h>

#include "mboot_api.h"
#include "mboot_dfu.h"
#include "mboot_region.h"
#include "mboot_usbd.h"

_Static_assert(MBOOT_MAX_ALT_SETTINGS <= MBOOT_USBD_MAX_ALT_SETTINGS,
    "region-layer alt-setting limit exceeds usbd descriptor buffer capacity");

// The target build includes the TinyUSB headers.  The host test harness builds
// with MBOOT_TESTS_FAKE_TUSB=1 and gets the minimum types and stubs from
// fake_tusb.h.
#ifdef MBOOT_TESTS_FAKE_TUSB
#include "fake_tusb.h"
#else
#include "tusb.h"
#include "class/dfu/dfu_device.h"
#endif


// MBOOT_USBD_XFER_SIZE and MBOOT_DFU_XFER_SIZE must match; a port that defines
// only one gets a build error here.
_Static_assert(MBOOT_USBD_XFER_SIZE == MBOOT_DFU_XFER_SIZE,
    "MBOOT_USBD_XFER_SIZE must equal MBOOT_DFU_XFER_SIZE");

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

// Write a uint16_t as two bytes (little-endian) at dst.
static inline void put_le16(uint8_t *dst, uint16_t v) {
    dst[0] = (uint8_t)(v & 0xFF);
    dst[1] = (uint8_t)(v >> 8);
}

// ---------------------------------------------------------------------------
// Static descriptor storage
// ---------------------------------------------------------------------------

// Device descriptor (18 bytes, fixed layout for DFU mode).
static uint8_t s_desc_device[18];

// Configuration descriptor (variable length, built at init time).
static uint8_t s_desc_cfg[MBOOT_USBD_CFG_DESC_MAX_LEN];
static uint16_t s_desc_cfg_len;

// String descriptors 0-3 (LANGID, manufacturer, product, serial).
// Each is a USB string descriptor: byte 0 = length, byte 1 = 0x03, then UTF-16LE.
#define STR_DESC_MAX_BYTES (2 + 126 * 2)
static uint8_t s_str_langid[4];
static uint8_t s_str_manufacturer[STR_DESC_MAX_BYTES];
static uint8_t s_str_product[STR_DESC_MAX_BYTES];
static uint8_t s_str_serial[STR_DESC_MAX_BYTES];

// Buffer for the current alt-setting string descriptor returned by
// tud_descriptor_string_cb.  TinyUSB copies the content before issuing
// another descriptor request, so a single file-scope buffer is safe.
static uint16_t s_alt_str_buf[1 + MBOOT_REGION_DESC_MAX_CONTENT_CHARS];

// Buffer for the vendor-erase DATA stage payload (MBOOT_VREQ_ERASE, 8 bytes).
// File scope so it is still there in the ACK stage of the same control
// transfer.
static uint8_t s_vendor_buf[8];

// Buffer for the MBOOT_VREQ_RESULT reply.
static uint8_t s_result_buf[MBOOT_VREQ_RESULT_LEN];

// bmRequestType of MBOOT_VREQ_ERASE (host to device, vendor, interface) and of
// MBOOT_VREQ_RESULT (device to host, vendor, interface).
#define MBOOT_VREQ_ERASE_REQTYPE (0x41u)
#define MBOOT_VREQ_RESULT_REQTYPE (0xC1u)

// Leave-request state.  TinyUSB owns the DFU state machine; this glue only
// needs to know when the host has manifested a successful download
// (tud_dfu_manifest_cb) and then issued the bus reset that ends the session
// (mboot_usbd_bus_reset).  On that reset the main loop is asked to leave DFU
// mode.
static bool s_manifest_complete;
static bool s_leave_requested;

// tud_mounted() as seen by the previous mboot_usbd_leave_requested() call.
static bool s_was_mounted;

// Count of host-driven callbacks, see mboot_usbd_activity().
static uint32_t s_activity;

// Last alt seen by a download/upload callback, used to detect a SET_INTERFACE
// from the host since the previous callback.  The sentinel 0xFF makes the first
// callback after init run the alt-switch path, which clears the touched-sector
// bitmap and commits the active region.  TinyUSB's DFU driver gives the
// application no hook on SET_INTERFACE, so the switch is only noticed at the
// next data callback.
#define MBOOT_ALT_UNSET (0xFFu)
static uint8_t s_last_alt = MBOOT_ALT_UNSET;

// ---------------------------------------------------------------------------
// Descriptor builder helpers
// ---------------------------------------------------------------------------

// Encode an ASCII string into a USB string descriptor (UTF-16LE, with the
// 2-byte header).  Returns the total descriptor length in bytes.
// dst must have at least STR_DESC_MAX_BYTES bytes.
static uint16_t encode_string_desc(uint8_t *dst, const char *src) {
    size_t slen = 0;

    if (src) {
        for (; slen < 126 && src[slen] != '\0'; slen++) {
            dst[2 + 2 * slen] = (uint8_t)src[slen];
            dst[3 + 2 * slen] = 0x00;
        }
    }

    uint16_t total = (uint16_t)(2 + slen * 2);
    dst[0] = (uint8_t)total;
    dst[1] = 0x03;
    return total;
}

// ---------------------------------------------------------------------------
// Initialisation
// ---------------------------------------------------------------------------

void mboot_usbd_init(void) {
    size_t alt_count = mboot_region_count();

    s_manifest_complete = false;
    s_leave_requested = false;
    s_was_mounted = false;

    // --- Device descriptor ---
    // bcdUSB=0x0110, device class/sub/proto=0/0/0 (per-interface),
    // bMaxPacketSize0=64, idVendor/idProduct from port, bcdDevice=0x0001,
    // iManufacturer=1, iProduct=2, iSerialNumber=3, bNumConfigurations=1.
    s_desc_device[0] = 18;
    s_desc_device[1] = 0x01;                    // bDescriptorType: device
    put_le16(&s_desc_device[2], 0x0110);         // bcdUSB
    s_desc_device[4] = 0x00;                    // bDeviceClass
    s_desc_device[5] = 0x00;                    // bDeviceSubClass
    s_desc_device[6] = 0x00;                    // bDeviceProtocol
    s_desc_device[7] = 64;                      // bMaxPacketSize0
    put_le16(&s_desc_device[8], mboot_port_get_vid());
    put_le16(&s_desc_device[10], mboot_port_get_pid());
    put_le16(&s_desc_device[12], 0x0001);        // bcdDevice
    s_desc_device[14] = 1;                      // iManufacturer
    s_desc_device[15] = 2;                      // iProduct
    s_desc_device[16] = 3;                      // iSerialNumber
    s_desc_device[17] = 1;                      // bNumConfigurations

    // --- Configuration descriptor ---
    // Layout: 9 (config hdr) + alt_count * 9 (interface) + 9 (DFU functional).
    uint16_t total_len = (uint16_t)(9u + alt_count * 9u + 9u);

    uint8_t *p = s_desc_cfg;

    // Configuration descriptor header (9 bytes).
    *p++ = 9;
    *p++ = 0x02;                                      // bDescriptorType: configuration
    *p++ = (uint8_t)(total_len & 0xFF);
    *p++ = (uint8_t)(total_len >> 8);
    *p++ = 1;                                         // bNumInterfaces
    *p++ = 1;                                         // bConfigurationValue
    *p++ = 0;                                         // iConfiguration
    *p++ = 0x80;                                      // bmAttributes: bus-powered
    *p++ = 50;                                        // bMaxPower: 100 mA

    // DFU interface alt-setting descriptors (one per alt, 9 bytes each).
    // bInterfaceClass=0xFE (Application Specific), bInterfaceSubClass=0x01 (DFU),
    // bInterfaceProtocol=0x02 (DFU mode), iInterface = 4 + alt_index.
    for (size_t alt = 0; alt < alt_count; alt++) {
        *p++ = 9;
        *p++ = 0x04;                                  // bDescriptorType: interface
        *p++ = MBOOT_DFU_INTERFACE_NUMBER;            // bInterfaceNumber
        *p++ = (uint8_t)alt;                          // bAlternateSetting
        *p++ = 0;                                     // bNumEndpoints
        *p++ = 0xFE;                                  // bInterfaceClass
        *p++ = 0x01;                                  // bInterfaceSubClass: DFU
        *p++ = 0x02;                                  // bInterfaceProtocol: DFU mode
        *p++ = (uint8_t)(4u + alt);                  // iInterface string index
    }

    // DFU functional descriptor (9 bytes).
    // bmAttributes: bit0=canDnload(1), bit1=canUpload(1), bit2=manifestTolerant(0).
    // bitManifestationTolerant=0 suits a bootloader: after manifestation the
    // device enters dfuMANIFEST-WAIT-RESET and must get a USB reset before
    // leaving.  TinyUSB's dfu_device.c honours this flag in
    // tud_dfu_finish_flashing().
    *p++ = 9;
    *p++ = 0x21;                                      // bDescriptorType: DFU functional
    *p++ = 0x03;                                      // bmAttributes: canDnload | canUpload
    *p++ = 0;                                         // wDetachTimeout low
    *p++ = 0;                                         // wDetachTimeout high
    *p++ = (uint8_t)(MBOOT_USBD_XFER_SIZE & 0xFF);
    *p++ = (uint8_t)(MBOOT_USBD_XFER_SIZE >> 8);
    *p++ = 0x10;                                      // bcdDFUVersion low
    *p++ = 0x01;                                      // bcdDFUVersion high

    s_desc_cfg_len = (uint16_t)(p - s_desc_cfg);

    // --- String descriptors 0-3 ---
    // Index 0: LANGID (English US = 0x0409).
    s_str_langid[0] = 4;
    s_str_langid[1] = 0x03;
    s_str_langid[2] = 0x09;
    s_str_langid[3] = 0x04;

    // Index 1: manufacturer.
    encode_string_desc(s_str_manufacturer, "MicroPython");

    // Index 2: product.
    encode_string_desc(s_str_product, mboot_port_get_product_string());

    // Index 3: serial number (ASCII from port -> UTF-16LE).
    char serial_buf[33];
    serial_buf[0] = '\0';
    mboot_port_get_serial_number(serial_buf, sizeof(serial_buf));
    encode_string_desc(s_str_serial, serial_buf);

    (void)s_desc_cfg_len;
}

// ---------------------------------------------------------------------------
// TinyUSB descriptor callbacks
// ---------------------------------------------------------------------------

uint8_t const *tud_descriptor_device_cb(void) {
    return s_desc_device;
}

uint8_t const *tud_descriptor_configuration_cb(uint8_t index) {
    (void)index;
    return s_desc_cfg;
}

// tud_descriptor_string_cb - return the USB string descriptor for index.
//
// Index allocation:
//   0: LANGID
//   1: Manufacturer ("MicroPython")
//   2: Product (mboot_port_get_product_string)
//   3: Serial (mboot_port_get_serial_number, ASCII -> UTF-16LE)
//   4..4+N-1: alt-setting strings (mboot_region_get_alt_string, includes header)
//
// mboot_region_get_alt_string() returns the alt-setting strings with the 2-byte
// USB string descriptor header already in place, and mboot_usbd_init() builds
// indices 0-3 with theirs.
uint16_t const *tud_descriptor_string_cb(uint8_t index, uint16_t langid) {
    (void)langid;

    if (index == 0) {
        return (uint16_t const *)s_str_langid;
    } else if (index == 1) {
        return (uint16_t const *)s_str_manufacturer;
    } else if (index == 2) {
        return (uint16_t const *)s_str_product;
    } else if (index == 3) {
        return (uint16_t const *)s_str_serial;
    } else {
        uint8_t alt = (uint8_t)(index - 4u);
        if ((size_t)alt >= mboot_region_count()) {
            return NULL;
        }
        int n = mboot_region_get_alt_string(alt,
            s_alt_str_buf,
            MBOOT_REGION_DESC_MAX_CONTENT_CHARS);
        if (n < 0) {
            return NULL;
        }
        return s_alt_str_buf;
    }
}

// ---------------------------------------------------------------------------
// TinyUSB DFU class callbacks
// ---------------------------------------------------------------------------

// sync_alt - commit a host-selected alt setting if it differs from the last
// alt seen by a data callback.
//
// TinyUSB's DFU class driver stores the new alt on SET_INTERFACE without
// telling the application, so the switch is noticed here, on the first
// DNLOAD/UPLOAD/MANIFEST/ABORT callback that carries the new value.  On a real
// switch it clears the touched-sector bitmap (via
// mboot_dfu_notify_set_interface), so the first write into the new region
// erases first instead of skipping a sector whose bit was set against the
// previous region's geometry.
//
// Returns 0 on success, -1 if alt is past the populated region table.
static int sync_alt(uint8_t alt) {
    s_activity++;
    if (alt == s_last_alt) {
        return 0;
    }
    if (mboot_region_set_active(alt) != 0) {
        return -1;
    }
    // notify_set_interface calls mboot_region_set_active(alt) again; that is
    // idempotent and cheaper than a separate entry point for the bitmap reset.
    mboot_dfu_notify_set_interface(alt);
    s_last_alt = alt;
    return 0;
}

// end_session - drop the download session of the alt setting that is active.
//
// The session state (the touched-sector bitmap and the session-begin hook
// state, both kept by mboot_dfu.c) describes sectors the host has written in
// the transfer it just started.  It is dropped when the transfer ends: in
// tud_dfu_download_cb when a block fails, in tud_dfu_manifest_cb (accepted or
// rejected) and in mboot_usbd_bus_reset().  The host then starts over from
// block 0, so every sector it writes again has to be erased again and the
// session-begin hook run again; keeping the bitmap would make the second
// transfer write onto programmed flash.  mboot_dfu_notify_set_interface()
// clears both, as a SET_INTERFACE or DFU_ABORT does.
static void end_session(uint8_t alt) {
    mboot_dfu_notify_set_interface(alt);
    s_last_alt = alt;
}

// tud_dfu_get_timeout_cb - bwPollTimeout for the next operation (ms).
//
// TinyUSB calls it when it answers the GETSTATUS request that moves the device
// to dfuDNBUSY (state 4) or to dfuMANIFEST.  It returns 100 ms for dfuDNBUSY
// (MBOOT_USBD_POLL_DNLOAD_MS, which a port can override) and 1000 ms
// (MBOOT_USBD_POLL_MANIFEST_MS) for the manifest step.
//
// The host waits this long after a DNLOAD block before asking for the status
// again (dfu-util sleeps the full time on every block).  The flash work of one
// block (an erase of a sector now and then, a program of up to 2 KiB) runs in
// the status request that reports dfuDNBUSY, and a status request that arrives
// while it is still running waits on the bus.  So a short time costs nothing
// on a slow flash, and a long one is multiplied by the block count.  The
// manifest step validates the whole image (hash and signature) and keeps the
// long value.
#define MBOOT_USBD_STATE_DNBUSY (4u)
#ifndef MBOOT_USBD_POLL_DNLOAD_MS
#define MBOOT_USBD_POLL_DNLOAD_MS (100u)
#endif
#define MBOOT_USBD_POLL_MANIFEST_MS (1000u)
uint32_t tud_dfu_get_timeout_cb(uint8_t alt, uint8_t state) {
    (void)alt;
    return state == MBOOT_USBD_STATE_DNBUSY ? MBOOT_USBD_POLL_DNLOAD_MS : MBOOT_USBD_POLL_MANIFEST_MS;
}

// Map a negative mboot_dfu_on_dnload() return value to a DFU 1.1 status code.
static uint8_t dnload_status(int rc) {
    if (rc == 0) {
        return DFU_STATUS_OK;
    }
    if (rc == MBOOT_DFU_ERR_SESSION_BEGIN) {
        return DFU_STATUS_ERR_ERASE;
    }
    if (rc == -ERANGE || rc == -EACCES) {
        return DFU_STATUS_ERR_ADDRESS;
    }
    return DFU_STATUS_ERR_WRITE;
}

// tud_dfu_download_cb - write a received DNLOAD block to flash.
//
// Goes through mboot_dfu_on_dnload, which checks the touched-sector bitmap and
// erases a sector on the first write to it in the session.  Padding with 0xFF
// to a multiple of MBOOT_DFU_WRITE_ALIGN and the staging buffer are also
// handled there.  A failed block ends the download session (end_session),
// because the host starts over.
void tud_dfu_download_cb(uint8_t alt, uint16_t block_num, uint8_t const *data, uint16_t length) {
    if (sync_alt(alt) != 0) {
        tud_dfu_finish_flashing(DFU_STATUS_ERR_TARGET);
        return;
    }
    int rc = mboot_dfu_on_dnload(block_num, data, length);
    if (rc != 0) {
        end_session(alt);
    }
    tud_dfu_finish_flashing(dnload_status(rc));
}

// tud_dfu_manifest_cb - DFU download sequence complete.
//
// mboot_hook_manifest() decides whether the download is accepted.  Its DFU
// status goes to TinyUSB, and a failure keeps the device in DFU mode
// (s_manifest_complete stays false).  The download session ends here either
// way, so a host that retries after a rejection starts a new one.
//
// bitManifestationTolerant=0 in the functional descriptor makes TinyUSB move
// the device to DFU_MANIFEST_WAIT_RESET once this callback returns.  The device
// then waits for a USB bus reset; mboot_usbd_leave_requested() sees it, runs
// mboot_usbd_bus_reset(), and that uses s_manifest_complete to decide whether
// to leave DFU mode.
void tud_dfu_manifest_cb(uint8_t alt) {
    if (sync_alt(alt) != 0) {
        tud_dfu_finish_flashing(DFU_STATUS_ERR_TARGET);
        return;
    }
    uint8_t status = mboot_hook_manifest(alt);
    if (status == DFU_STATUS_OK) {
        s_manifest_complete = true;
    }
    end_session(alt);
    tud_dfu_finish_flashing(status);
}

// tud_dfu_upload_cb - fill a UPLOAD block from flash.
//
// Reads up to length bytes from the active region at block_num * XFER_SIZE.
// Past the end of the region it returns fewer bytes (or 0), which ends the
// upload per DFU 1.1 §6.2 (short-read rule).
uint16_t tud_dfu_upload_cb(uint8_t alt, uint16_t block_num, uint8_t *data, uint16_t length) {
    if (sync_alt(alt) != 0) {
        return 0;
    }
    mboot_addr_t base = mboot_region_active_base();
    mboot_addr_t size = mboot_region_active_size();
    mboot_addr_t addr = base + (mboot_addr_t)block_num * MBOOT_USBD_XFER_SIZE;

    if (size == 0 || addr >= base + size) {
        return 0;
    }

    mboot_addr_t avail = (base + size) - addr;
    uint16_t read_len = length;
    if ((mboot_addr_t)read_len > avail) {
        read_len = (uint16_t)avail;
    }

    int rc = mboot_region_read(addr, data, read_len);
    return (rc == 0) ? read_len : 0;
}

// tud_dfu_abort_cb - DFU_ABORT acknowledged.
//
// Drops a partial download: mboot_dfu_notify_set_interface() clears the
// touched-sector bitmap and the session-begin state of the alt setting, so a
// restarted download erases its sectors again and runs the session-begin hook
// again.
void tud_dfu_abort_cb(uint8_t alt) {
    s_activity++;
    if (mboot_region_set_active(alt) != 0) {
        return;
    }
    mboot_dfu_notify_set_interface(alt);
    s_last_alt = alt;
}

// tud_dfu_detach_cb - DFU_DETACH acknowledged (no-op in DFU mode).
//
// DFU_DETACH is a runtime-mode request.  In DFU mode the device is already in
// DFU mode and cannot detach to the application, so nothing happens.
void tud_dfu_detach_cb(void) {
}

// mboot_usbd_bus_reset - the host reset the bus or went away (see
// mboot_usbd_leave_requested()).
//
// A reset after tud_dfu_manifest_cb is the host's acknowledgement that the
// download session is over.  It sets s_leave_requested so the main loop, which
// polls mboot_usbd_leave_requested(), leaves DFU mode.  Other resets belong to
// enumeration or to a host that went away and came back; the download session
// of the earlier connection ends and TinyUSB's DFU alt setting is 0 again.
//
// The reset counts as host activity.  An open session suppresses the
// inactivity timeout, so without this the deadline of a session that was open
// for longer than the timeout would expire the moment the session ends, and
// the bootloader would reset before the returning host could do anything.
void mboot_usbd_bus_reset(void) {
    s_activity++;
    if (s_manifest_complete) {
        s_leave_requested = true;
    }
    end_session(0);
}

// ---------------------------------------------------------------------------
// Vendor request handlers: opcodes 0x80 (MBOOT_VREQ_ERASE) and 0x81
// (MBOOT_VREQ_RESULT)
// ---------------------------------------------------------------------------

// tud_vendor_control_xfer_cb - handle vendor control transfers.
//
// Vendor requests arrive here through TinyUSB's vendor-request dispatch.
// bmRequestType type bits 6:5 = 0b10 is TUSB_REQ_TYPE_VENDOR, so the DFU class
// driver never sees them (TinyUSB usbd.c process_setup_received).
//
// MBOOT_VREQ_ERASE (bmRequestType 0x41, host to device):
//   SETUP stage: validate the request and accept the 8-byte DATA stage.
//   DATA stage: pass the payload to mboot_dfu_on_vendor_request.  A failure
//   returns false, which stalls the status stage so the host sees the request
//   fail (TinyUSB ignores the result of the ACK stage, which follows it).
// MBOOT_VREQ_RESULT (bmRequestType 0xC1, device to host):
//   SETUP stage: fetch the 16-byte reply from mboot_hook_get_result and send it.
// Any other combination is rejected (return false = STALL).
bool tud_vendor_control_xfer_cb(uint8_t rhport, uint8_t stage, tusb_control_request_t const *request) {
    if (request->wIndex != MBOOT_DFU_INTERFACE_NUMBER) {
        return false;
    }
    if (stage == CONTROL_STAGE_SETUP) {
        s_activity++;
    }

    if (request->bmRequestType == MBOOT_VREQ_RESULT_REQTYPE && request->bRequest == MBOOT_VREQ_RESULT) {
        if (stage == CONTROL_STAGE_SETUP) {
            if (request->wLength != MBOOT_VREQ_RESULT_LEN) {
                return false;
            }
            memset(s_result_buf, 0, sizeof(s_result_buf));
            if (mboot_hook_get_result(s_result_buf, sizeof(s_result_buf)) != MBOOT_VREQ_RESULT_LEN) {
                return false;
            }
            return tud_control_xfer(rhport, request, s_result_buf, MBOOT_VREQ_RESULT_LEN);
        }
        return true;
    }

    if (request->bmRequestType != MBOOT_VREQ_ERASE_REQTYPE || request->bRequest != MBOOT_VREQ_ERASE) {
        return false;
    }

    if (stage == CONTROL_STAGE_SETUP) {
        if (request->wLength != 8) {
            return false;
        }
        // Ask for the 8-byte DATA stage into s_vendor_buf.
        return tud_control_xfer(rhport, request, s_vendor_buf, 8);
    } else if (stage == CONTROL_STAGE_DATA) {
        // s_vendor_buf holds the 8-byte payload from the DATA stage.  Stall
        // the control transfer on any erase failure so the host learns it
        // failed instead of programming an unerased sector; pydfu in pure-DFU
        // mode does not issue a follow-up GETSTATUS.
        int rc = mboot_dfu_on_vendor_request(MBOOT_VREQ_ERASE,
            request->wValue, request->wIndex,
            s_vendor_buf, 8);
        return rc == 0;
    }

    // ACK stage: the status stage has been sent, nothing left to do.
    return true;
}

// ---------------------------------------------------------------------------
// Leave request
// ---------------------------------------------------------------------------

// mboot_usbd_leave_requested - poll for the end of the DFU session.
//
// The DFU class driver of lib/tinyusb resets its own state machine on a USB bus
// reset but gives the application no callback for it, so the reset the host
// sends after the manifest step cannot be seen through the DFU callbacks.
// This function, called from the main loop, polls tud_mounted() instead.
// TinyUSB drops the configuration on a bus reset and on a disconnect, so a
// device that was mounted at the previous call and is not now has seen one of
// the two, and mboot_usbd_bus_reset() is run for it.  That function latches
// the leave request only after a successful manifest, so the resets of
// enumeration and of a host that goes away and comes back do not make the
// bootloader leave.  Once latched, the request stays set until
// mboot_usbd_init().
bool mboot_usbd_leave_requested(void) {
    bool mounted = tud_mounted();
    if (s_was_mounted && !mounted) {
        mboot_usbd_bus_reset();
    }
    s_was_mounted = mounted;
    return s_leave_requested;
}

uint32_t mboot_usbd_activity(void) {
    return s_activity;
}
