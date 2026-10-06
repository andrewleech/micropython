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

// fake_tusb.h - minimal TinyUSB surface for the mboot_usbd host test build.
//
// mboot_usbd.c includes this instead of the TinyUSB headers when
// MBOOT_TESTS_FAKE_TUSB=1. It has just the types and stubs mboot_usbd.c uses.
// mboot_usbd.c compiles against either set.
//
// The tud_control_xfer, tud_control_status and tud_dfu_finish_flashing stubs
// record the arguments of the most recent call in globals for the tests to
// check.

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
#define DFU_STATUS_ERR_ADDRESS (0x08u)

// ---------------------------------------------------------------------------
// tud_control_xfer / tud_control_status stubs
//
// Each stub records its arguments in the fake_tusb_last_* globals.
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

// Value returned by tud_mounted().
extern bool fake_tusb_mounted;

// Clear the recorded calls.
void fake_tusb_reset(void);

bool tud_control_xfer(uint8_t rhport, const tusb_control_request_t *request,
    void *buffer, uint16_t len);
bool tud_control_status(uint8_t rhport, const tusb_control_request_t *request);

// Called by mboot_usbd.c after download/manifest.
void tud_dfu_finish_flashing(uint8_t status);

// Polled by mboot_usbd_leave_requested().
bool tud_mounted(void);

// ---------------------------------------------------------------------------
// mboot_usbd.c callbacks, called directly by test_usbd.c
// ---------------------------------------------------------------------------

// Descriptor callbacks.
uint8_t const *tud_descriptor_device_cb(void);
uint8_t const *tud_descriptor_configuration_cb(uint8_t index);
uint16_t const *tud_descriptor_string_cb(uint8_t index, uint16_t langid);

// DFU class callbacks.
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
