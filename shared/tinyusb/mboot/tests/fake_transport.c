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

// fake_transport.c — thin wrappers over mboot_dfu_on_* for test use.

#include <stdint.h>

#include "mboot_api.h"
#include "mboot_dfu.h"
#include "fake_transport.h"

int fake_dfu_dnload(uint16_t wBlockNum, const uint8_t *data, uint16_t wLength) {
    return mboot_dfu_on_dnload(wBlockNum, data, wLength);
}

int fake_vendor_erase(uint16_t wValue, uint32_t addr, uint32_t length) {
    uint8_t payload[8];
    // addr LE
    payload[0] = (uint8_t)(addr & 0xFF);
    payload[1] = (uint8_t)((addr >> 8) & 0xFF);
    payload[2] = (uint8_t)((addr >> 16) & 0xFF);
    payload[3] = (uint8_t)((addr >> 24) & 0xFF);
    // length LE
    payload[4] = (uint8_t)(length & 0xFF);
    payload[5] = (uint8_t)((length >> 8) & 0xFF);
    payload[6] = (uint8_t)((length >> 16) & 0xFF);
    payload[7] = (uint8_t)((length >> 24) & 0xFF);
    return mboot_dfu_on_vendor_request(MBOOT_VREQ_ERASE, wValue, 0, payload, 8);
}
