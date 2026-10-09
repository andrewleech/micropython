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

#ifndef MICROPY_INCLUDED_SHARED_MCUBOOT_MCUBOOT_REQUEST_H
#define MICROPY_INCLUDED_SHARED_MCUBOOT_MCUBOOT_REQUEST_H

#include <stddef.h>
#include <stdint.h>

#include "mcuboot_port.h"

// Handoff from the app to the bootloader through a no-init RAM region at MCUBOOT_REQ_START
// and a retention word. The region is MCUBOOT_REQ_REGION_SIZE bytes; the last 16 bytes of it
// (MCUBOOT_FI_STATE_OFFSET) belong to the test fault-injection state and are not part of the
// request or its CRC.

#define MCUBOOT_REQ_REGION_SIZE 0x400u
#define MCUBOOT_FI_STATE_OFFSET 0x3F0u
#define MCUBOOT_FI_STATE_SIZE   16u
_Static_assert(MCUBOOT_FI_STATE_OFFSET + MCUBOOT_FI_STATE_SIZE == MCUBOOT_REQ_REGION_SIZE,
    "the fault-injection state ends the request region");

#define MCUBOOT_REQ_MAGIC     0x5152424Du   // bytes "MBRQ"
#define MCUBOOT_REQ_VERSION   1u
#define MCUBOOT_REQ_ELEMS_MAX 988u

// Retention word: the upper 24 bits hold the key, bit 7 of the low byte means that FSLOAD
// elements are in the request region. Ports whose retention register is narrower than 32
// bits (MCUBOOT_RETENTION_BITS) store MCUBOOT_RET_8BIT_DFU or MCUBOOT_RET_8BIT_FSLOAD.
#define MCUBOOT_RET_KEY        0x70AD0000u
#define MCUBOOT_RET_KEY_MASK   0xFFFFFF00u
#define MCUBOOT_RET_FSLOAD     0x80u
#define MCUBOOT_RET_8BIT_DFU   0xADu
#define MCUBOOT_RET_8BIT_FSLOAD 0xAEu

// Values equal mboot_reset_mode_t BOOTLOADER = 1 and BOOTLOADER_FSLOAD = 2.
typedef enum {
    MCUBOOT_REQ_NONE = 0,
    MCUBOOT_REQ_DFU = 1,
    MCUBOOT_REQ_FSLOAD = 2,
} mcuboot_req_mode_t;

typedef struct {
    uint32_t magic;         // MCUBOOT_REQ_MAGIC
    uint16_t version;       // MCUBOOT_REQ_VERSION
    uint16_t mode;          // mcuboot_req_mode_t
    uint32_t seq;           // reserved; must be zero
    uint16_t elems_len;     // valid bytes in elems
    uint16_t flags;         // 0
    uint32_t crc32;         // CRC-32/ISO-HDLC over the 20 header bytes with crc32 = 0, then elems[0..elems_len)
    uint8_t elems[MCUBOOT_REQ_ELEMS_MAX];
} mcuboot_request_t;

_Static_assert(sizeof(mcuboot_request_t) == 1008, "request struct size");
_Static_assert(sizeof(mcuboot_request_t) <= MCUBOOT_FI_STATE_OFFSET, "the request ends before the fault-injection state");

// Zeroes the request struct in the region (not the fault-injection state behind it) with
// 64-bit stores, so that RAM with ECC is initialised in full words, and clears the retention
// word.
void mcuboot_request_clear(void);

// App role. Writes the request into the region with 64-bit stores, writes the retention word
// and resets through mcuboot_port_reset(). len must not exceed MCUBOOT_REQ_ELEMS_MAX; a
// longer element stream is truncated to nothing and the request becomes a plain DFU request.
MCUBOOT_NORETURN void mcuboot_request_set_and_reset(mcuboot_req_mode_t mode, const uint8_t *elems, size_t len);

// Bootloader role. Consumes the request: after a power-on reset the region and the retention
// word are cleared and out->mode is MCUBOOT_REQ_NONE. Otherwise a request whose magic,
// version, length, mode and CRC are valid is copied to out; with an invalid request but a
// matching retention word out->mode is MCUBOOT_REQ_DFU without elements. In every case the
// request fields of the region and the retention word are zero when the function returns.
void mcuboot_request_take(mcuboot_request_t *out, uint32_t reset_cause);

#endif // MICROPY_INCLUDED_SHARED_MCUBOOT_MCUBOOT_REQUEST_H
