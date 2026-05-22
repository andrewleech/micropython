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

#ifndef MBOOT_TESTS_FAKE_TUSB_H
#define MBOOT_TESTS_FAKE_TUSB_H

// fake_tusb.h — minimal TinyUSB surface for the mboot_usbd host test build.
//
// This header is included by mboot_usbd.c when MBOOT_TESTS_FAKE_TUSB=1.
// It provides the minimum types and stubs that mboot_usbd.c requires from
// the TinyUSB headers, so the host tests build without the real TinyUSB stack.
//
// The same C source (mboot_usbd.c) must compile against both this fake header
// and the real TinyUSB headers.  Fake declarations are gated on
// MBOOT_TESTS_FAKE_TUSB so that when the flag is absent the real headers win.
//
// The fake stubs for tud_control_xfer and tud_control_status record the
// arguments of the most recent call into global variables that the tests can
// assert against.

#include <stdbool.h>
#include <stdint.h>
#include <string.h>

// ---------------------------------------------------------------------------
// CONTROL_STAGE_* constants (mirrors tusb_types.h)
// ---------------------------------------------------------------------------

typedef enum {
    CONTROL_STAGE_IDLE = 0,
    CONTROL_STAGE_SETUP,
    CONTROL_STAGE_DATA,
    CONTROL_STAGE_ACK,
} fake_control_stage_t;

// Re-export using the names mboot_usbd.c expects.
#define CONTROL_STAGE_IDLE  CONTROL_STAGE_IDLE
#define CONTROL_STAGE_SETUP CONTROL_STAGE_SETUP
#define CONTROL_STAGE_DATA  CONTROL_STAGE_DATA
#define CONTROL_STAGE_ACK   CONTROL_STAGE_ACK

// ---------------------------------------------------------------------------
// tusb_control_request_t (mirrors tusb_types.h layout, packed)
// ---------------------------------------------------------------------------

typedef struct __attribute__((packed)) {
    uint8_t bmRequestType;
    uint8_t bRequest;
    uint16_t wValue;
    uint16_t wIndex;
    uint16_t wLength;
} tusb_control_request_t;

// ---------------------------------------------------------------------------
// DFU status codes needed by tud_dfu_finish_flashing
// ---------------------------------------------------------------------------

#define DFU_STATUS_OK         (0x00u)
#define DFU_STATUS_ERR_TARGET (0x01u)
#define DFU_STATUS_ERR_WRITE  (0x03u)
#define DFU_STATUS_ERR_ERASE  (0x04u)

// ---------------------------------------------------------------------------
// tud_control_xfer / tud_control_status stubs
//
// The stubs record the rhport, request pointer, buffer pointer, and length of
// the most recent call.  Tests can read these via the fake_tusb_last_* globals.
// ---------------------------------------------------------------------------

// Last call record for tud_control_xfer.
extern uint8_t fake_tusb_last_xfer_rhport;
extern const tusb_control_request_t *fake_tusb_last_xfer_request;
extern void *fake_tusb_last_xfer_buf;
extern uint16_t fake_tusb_last_xfer_len;
extern bool fake_tusb_last_xfer_called;

// Last call record for tud_control_status.
extern uint8_t fake_tusb_last_status_rhport;
extern const tusb_control_request_t *fake_tusb_last_status_request;
extern bool fake_tusb_last_status_called;

// Last call record for tud_dfu_finish_flashing.
extern uint8_t fake_tusb_last_finish_status;
extern bool fake_tusb_last_finish_called;

// Reset all recorded state.
void fake_tusb_reset(void);

// Stub implementations (defined in fake_tusb.c).
bool tud_control_xfer(uint8_t rhport, const tusb_control_request_t *request,
    void *buffer, uint16_t len);
bool tud_control_status(uint8_t rhport, const tusb_control_request_t *request);

// tud_dfu_finish_flashing — called by mboot_usbd.c after download/manifest.
void tud_dfu_finish_flashing(uint8_t status);

// ---------------------------------------------------------------------------
// Declarations for mboot_usbd.c callbacks (tested directly by test_usbd.c)
// ---------------------------------------------------------------------------

// Descriptor callbacks.
uint8_t const *tud_descriptor_device_cb(void);
uint8_t const *tud_descriptor_configuration_cb(uint8_t index);
uint16_t const *tud_descriptor_string_cb(uint8_t index, uint16_t langid);

// DFU class callbacks.
bool tud_dfu_set_alt_cb(uint8_t alt);
uint32_t tud_dfu_get_timeout_cb(uint8_t alt, uint8_t state);
void tud_dfu_download_cb(uint8_t alt, uint16_t block_num, uint8_t const *data, uint16_t length);
void tud_dfu_manifest_cb(uint8_t alt);
uint16_t tud_dfu_upload_cb(uint8_t alt, uint16_t block_num, uint8_t *data, uint16_t length);
void tud_dfu_abort_cb(uint8_t alt);
void tud_dfu_detach_cb(void);

// Vendor control transfer callback.
bool tud_vendor_control_xfer_cb(uint8_t rhport, uint8_t stage,
    tusb_control_request_t const *request);

#endif // MBOOT_TESTS_FAKE_TUSB_H
