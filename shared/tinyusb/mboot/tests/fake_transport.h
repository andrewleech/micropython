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

#ifndef MBOOT_TESTS_FAKE_TRANSPORT_H
#define MBOOT_TESTS_FAKE_TRANSPORT_H

// fake_transport.h — drive mboot_dfu_on_* without TinyUSB.
//
// The helpers here call mboot_dfu.h entry points directly so that tests can
// exercise the flash-dispatch helper using plain function calls rather than a
// USB stack.  TinyUSB owns the DFU state machine on-target; the host tests
// only exercise the flash/bitmap dispatch path.

#include <stdint.h>

// fake_dfu_dnload — issue a DFU_DNLOAD request.
//
// wBlockNum: block index (address = region_base + wBlockNum * transfer_size).
// data: payload bytes (may be NULL when wLength == 0).
// wLength: payload byte count; 0 signals end-of-transfer.
// Returns the mboot_dfu_on_dnload return value.
int fake_dfu_dnload(uint16_t wBlockNum, const uint8_t *data, uint16_t wLength);

// fake_vendor_erase — issue a vendor erase request (bRequest = 0x80).
//
// wValue: alt setting (0 = active).
// addr: start address (packed into 4-byte LE payload).
// length: byte count (0xFFFFFFFF for mass erase).
// Returns 0 on success, negative on error.
int fake_vendor_erase(uint16_t wValue, uint32_t addr, uint32_t length);

#endif // MBOOT_TESTS_FAKE_TRANSPORT_H
