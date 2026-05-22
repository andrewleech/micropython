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

// mboot_usbd.c — TinyUSB USB device glue for shared/tinyusb/mboot.
//
// Compile constraints: libc + mboot_api.h + mboot_usbd.h + mboot_dfu.h +
// mboot_region.h + TinyUSB src/ headers.
// No py/, extmod/, shared/runtime/, or port-specific headers.

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

// TinyUSB headers are included when building for the target.  When building
// the host test harness with MBOOT_TESTS_FAKE_TUSB=1, fake_tusb.h provides
// the minimum types and stubs needed.
#ifdef MBOOT_TESTS_FAKE_TUSB
#include "fake_tusb.h"
#else
#include "tusb.h"
#include "class/dfu/dfu_device.h"
#endif


// Sanity check: MBOOT_USBD_XFER_SIZE and MBOOT_DFU_XFER_SIZE must match.
// A port that defines one but not the other will get a build error here.
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
// Declared at file scope so it is accessible across the SETUP and ACK stages
// of the same control transfer without relying on local-variable lifetime.
static uint8_t s_vendor_buf[8];

// Leave-request bookkeeping.  TinyUSB owns the DFU state machine; this glue
// only needs to know when the host has manifested a successful download
// (tud_dfu_manifest_cb) and then issued the bus reset that ends the session
// (tud_dfu_reset_cb).  On that reset, request the main loop to exit DFU mode.
static bool s_manifest_complete;
static bool s_leave_requested;

// Last alt seen by a download/upload callback, used to detect a host-issued
// SET_INTERFACE since the previous callback.  Sentinel 0xFF forces the first
// callback after init to run the alt-switch path so the touched-sector bitmap
// is cleared and the active region is committed.  TinyUSB's DFU driver does
// not give the application a hook on SET_INTERFACE itself, so the switch is
// detected lazily at the next data callback.
#define MBOOT_ALT_UNSET (0xFFu)
static uint8_t s_last_alt = MBOOT_ALT_UNSET;

// ---------------------------------------------------------------------------
// Descriptor builder helpers
// ---------------------------------------------------------------------------

// Encode an ASCII string into a USB string descriptor (UTF-16LE, with the
// 2-byte header).  Returns the total descriptor length in bytes.
// dst must have at least STR_DESC_MAX_BYTES bytes.
//
// Reads source characters in a single ascending pass using word-aligned loads
// (LDR) rather than byte loads (LDRB).  Both constraints are necessary for
// correct operation when the source is in XIP flash with D-Cache disabled:
//
// 1. Word reads: LDRB at certain flash addresses returns 0xFF due to a
//    FlexSPI AHB byte-extraction limitation; LDR (word) reads are correct.
//
// 2. Single ascending pass: calling strlen() then re-reading from the start
//    causes the FlexSPI AHB to seek backward, which corrupts the first word
//    read after the backward jump.  Reading each character once in order
//    avoids this.
__attribute__((noinline)) MBOOT_RAM_FUNC_ATTR
static uint16_t encode_string_desc(uint8_t *dst, const char *src) {
    uint8_t *p = dst + 2;
    size_t slen = 0;

    if (src) {
        while (slen < 126) {
            uintptr_t addr = (uintptr_t)(src + slen);
            uint32_t word;
            __builtin_memcpy(&word, (const void *)(addr & ~(uintptr_t)3), sizeof(word));
            uint8_t ch = (uint8_t)(word >> ((addr & 3u) * 8u));
            if (!ch) {
                break;
            }
            *p++ = ch;
            *p++ = 0x00;
            slen++;
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
    // bitManifestationTolerant=0 is correct for a bootloader: after manifestation
    // the device enters dfuMANIFEST-WAIT-RESET and must receive a USB reset before
    // leaving.  TinyUSB's dfu_device.c honours this flag in tud_dfu_finish_flashing().
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

// tud_descriptor_string_cb — return the USB string descriptor for index.
//
// Index allocation:
//   0: LANGID
//   1: Manufacturer ("MicroPython")
//   2: Product (mboot_port_get_product_string)
//   3: Serial (mboot_port_get_serial_number, ASCII -> UTF-16LE)
//   4..4+N-1: alt-setting strings (mboot_region_get_alt_string, includes header)
//
// Alt-setting strings come pre-formatted with the 2-byte USB string descriptor
// header from mboot_region_get_alt_string().  Indices 0-3 are assembled in
// mboot_usbd_init() with their headers already in place.
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

// sync_alt — commit a host-selected alt-setting if it differs from the last
// alt seen by a data callback.
//
// TinyUSB's DFU class driver writes the new alt into its internal state on
// SET_INTERFACE without notifying the application, so the switch is detected
// here on the first DNLOAD/UPLOAD/MANIFEST/ABORT callback that carries the
// new value.  On a real switch this clears the touched-sector bitmap (via
// mboot_dfu_notify_set_interface) so the first write into the new region
// triggers a fresh erase rather than skipping one whose bitmap bit was set
// against the previous region's geometry.
//
// Returns 0 on success, -EINVAL if alt is past the populated region table.
static int sync_alt(uint8_t alt) {
    if (alt == s_last_alt) {
        return 0;
    }
    if (mboot_region_set_active(alt) != 0) {
        return -1;
    }
    // notify_set_interface also calls mboot_region_set_active(alt); that is
    // idempotent and cheaper than threading the bitmap reset through a new
    // entry point.
    mboot_dfu_notify_set_interface(alt);
    s_last_alt = alt;
    return 0;
}

// tud_dfu_get_timeout_cb — bwPollTimeout for the next operation (ms).
//
// 1000 ms is a conservative bound for sector erase + page program on the
// slowest NOR flash (typical sector erase: 50-500 ms).
uint32_t tud_dfu_get_timeout_cb(uint8_t alt, uint8_t state) {
    (void)alt;
    (void)state;
    return 1000;
}

// tud_dfu_download_cb — write a received DNLOAD block to flash.
//
// Routes through mboot_dfu_on_dnload, which consults the touched-sector
// bitmap and issues an implicit erase on the first write to each sector
// in the session.  Padding to a 4-byte boundary and the staging buffer are
// handled inside mboot_dfu_on_dnload.
void tud_dfu_download_cb(uint8_t alt, uint16_t block_num, uint8_t const *data, uint16_t length) {
    if (sync_alt(alt) != 0) {
        tud_dfu_finish_flashing(DFU_STATUS_ERR_TARGET);
        return;
    }
    int rc = mboot_dfu_on_dnload(block_num, data, length);
    tud_dfu_finish_flashing(rc == 0 ? DFU_STATUS_OK : DFU_STATUS_ERR_WRITE);
}

// tud_dfu_manifest_cb — DFU download sequence complete.
//
// bitManifestationTolerant=0 in the functional descriptor means TinyUSB
// drives the device into DFU_MANIFEST_WAIT_RESET after this callback returns.
// The device waits for a USB bus reset; tud_dfu_reset_cb() fires on that
// reset and uses s_manifest_complete to decide whether to leave DFU mode.
void tud_dfu_manifest_cb(uint8_t alt) {
    if (sync_alt(alt) != 0) {
        tud_dfu_finish_flashing(DFU_STATUS_ERR_TARGET);
        return;
    }
    s_manifest_complete = true;
    tud_dfu_finish_flashing(DFU_STATUS_OK);
}

// tud_dfu_upload_cb — fill a UPLOAD block from flash.
//
// Reads up to length bytes from the active region at block_num * XFER_SIZE.
// Returns fewer bytes (or 0) past the end of the region to signal end-of-upload
// per DFU 1.1 §6.2 (short-read rule).
uint16_t tud_dfu_upload_cb(uint8_t alt, uint16_t block_num, uint8_t *data, uint16_t length) {
    if (sync_alt(alt) != 0) {
        return 0;
    }
    #if MBOOT_ENABLE_PACKING
    // When firmware packing is enabled the host has no useful access to the
    // plaintext or ciphertext of the application region, so refuse UPLOAD.
    return 0;
    #else
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
    #endif
}

// tud_dfu_abort_cb — DFU_ABORT acknowledged.
//
// Resets the touched-sector bitmap for the current alt via
// mboot_dfu_notify_set_interface (which calls bitmap_clear + set_active).
// Force the next data callback's sync_alt to run the full switch path so a
// partial download is dropped cleanly.
void tud_dfu_abort_cb(uint8_t alt) {
    if (mboot_region_set_active(alt) != 0) {
        return;
    }
    mboot_dfu_notify_set_interface(alt);
    s_last_alt = alt;
}

// tud_dfu_detach_cb — DFU_DETACH acknowledged (no-op in DFU mode).
//
// DFU_DETACH is a runtime-mode request; in DFU mode the device is already
// in DFU mode and cannot detach to the application.  No action is taken.
void tud_dfu_detach_cb(void) {
}

// tud_dfu_reset_cb — USB bus reset received.
//
// A reset after tud_dfu_manifest_cb is the host's acknowledgement that the
// download session is over.  Set s_leave_requested so the main loop calls
// mboot_port_leave() on its next iteration.  Resets at any other time are
// part of normal enumeration and are ignored.
void tud_dfu_reset_cb(void) {
    if (s_manifest_complete) {
        s_leave_requested = true;
    }
}

// ---------------------------------------------------------------------------
// Vendor request handler: opcode 0x80 (MBOOT_VREQ_ERASE)
// ---------------------------------------------------------------------------

// tud_vendor_control_xfer_cb — handle vendor control transfers.
//
// Vendor request 0x80 (bmRequestType=0x41, vendor type, interface recipient)
// lands here via TinyUSB's vendor-request dispatch.  bmRequestType=0x41 has
// type bits 6:5 = 0b10, which is TUSB_REQ_TYPE_VENDOR; the DFU class driver
// never sees it.  TinyUSB's device core (usbd.c:783) routes vendor-typed
// requests directly to tud_vendor_control_xfer_cb.
//
// SETUP stage: validate the request and accept the 8-byte DATA stage.
// ACK stage: dispatch the received payload to mboot_dfu_on_vendor_request.
// All other combinations are rejected (return false = STALL).
bool tud_vendor_control_xfer_cb(uint8_t rhport, uint8_t stage, tusb_control_request_t const *request) {
    // Filter: vendor-typed, interface-recipient, opcode 0x80, DFU interface.
    if (request->bmRequestType != 0x41 || request->bRequest != MBOOT_VREQ_ERASE) {
        return false;
    }
    if (request->wIndex != MBOOT_DFU_INTERFACE_NUMBER) {
        return false;
    }

    if (stage == CONTROL_STAGE_SETUP) {
        if (request->wLength != 8) {
            return false;
        }
        // Request the 8-byte DATA stage into s_vendor_buf.
        return tud_control_xfer(rhport, request, s_vendor_buf, 8);
    } else if (stage == CONTROL_STAGE_ACK) {
        // s_vendor_buf now contains the 8-byte payload from the DATA stage.
        // STALL the control transfer on any erase failure so the host learns
        // the request failed instead of silently programming an unerased
        // sector; pydfu in pure-DFU mode does not issue a follow-up GETSTATUS.
        int rc = mboot_dfu_on_vendor_request(MBOOT_VREQ_ERASE,
            request->wValue, request->wIndex,
            s_vendor_buf, 8);
        return rc == 0;
    }

    // DATA stage: TinyUSB fills s_vendor_buf; nothing to do here.
    return true;
}

// ---------------------------------------------------------------------------
// Leave request
// ---------------------------------------------------------------------------

bool mboot_usbd_leave_requested(void) {
    return s_leave_requested;
}
