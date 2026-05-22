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

// fake_tusb.c — stub implementations for the minimal TinyUSB surface used by
// mboot_usbd.c in the host test harness.

#include "fake_tusb.h"

// ---------------------------------------------------------------------------
// Stub call-record globals
// ---------------------------------------------------------------------------

uint8_t fake_tusb_last_xfer_rhport;
const tusb_control_request_t *fake_tusb_last_xfer_request;
void *fake_tusb_last_xfer_buf;
uint16_t fake_tusb_last_xfer_len;
bool fake_tusb_last_xfer_called;

uint8_t fake_tusb_last_status_rhport;
const tusb_control_request_t *fake_tusb_last_status_request;
bool fake_tusb_last_status_called;

uint8_t fake_tusb_last_finish_status;
bool fake_tusb_last_finish_called;

// ---------------------------------------------------------------------------
// Reset
// ---------------------------------------------------------------------------

void fake_tusb_reset(void) {
    fake_tusb_last_xfer_rhport = 0;
    fake_tusb_last_xfer_request = NULL;
    fake_tusb_last_xfer_buf = NULL;
    fake_tusb_last_xfer_len = 0;
    fake_tusb_last_xfer_called = false;

    fake_tusb_last_status_rhport = 0;
    fake_tusb_last_status_request = NULL;
    fake_tusb_last_status_called = false;

    fake_tusb_last_finish_status = 0xFF;
    fake_tusb_last_finish_called = false;
}

// ---------------------------------------------------------------------------
// Stub implementations
// ---------------------------------------------------------------------------

bool tud_control_xfer(uint8_t rhport, const tusb_control_request_t *request,
    void *buffer, uint16_t len) {
    fake_tusb_last_xfer_rhport = rhport;
    fake_tusb_last_xfer_request = request;
    fake_tusb_last_xfer_buf = buffer;
    fake_tusb_last_xfer_len = len;
    fake_tusb_last_xfer_called = true;
    return true;
}

bool tud_control_status(uint8_t rhport, const tusb_control_request_t *request) {
    fake_tusb_last_status_rhport = rhport;
    fake_tusb_last_status_request = request;
    fake_tusb_last_status_called = true;
    return true;
}

void tud_dfu_finish_flashing(uint8_t status) {
    fake_tusb_last_finish_status = status;
    fake_tusb_last_finish_called = true;
}
