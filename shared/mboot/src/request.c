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

#include <stdbool.h>
#include <string.h>

#include "mcuboot_config/mcuboot_config.h"
#include "mboot_crc32.h"
#include "mboot_port.h"
#include "mboot_request.h"
#include "mboot_updatelog.h"

// The request struct (1008 bytes) is followed by the 16 bytes of the test fault injection
// state, which must survive a request being taken.
#define REGION_SIZE MBOOT_REQ_REGION_SIZE
#define REQUEST_SIZE (sizeof(mboot_request_t))

// Region access is done with 64-bit loads and stores: RAM with ECC needs whole words written
// before it is read.
static void region_store(void *dst, const void *src, size_t n) {
    volatile uint64_t *d = dst;
    const uint8_t *s = src;
    for (size_t i = 0; i < n / 8; i++) {
        uint64_t w;
        memcpy(&w, s + i * 8, 8);
        d[i] = w;
    }
}

static void region_load(void *dst, const volatile void *src, size_t n) {
    const volatile uint64_t *s = src;
    uint8_t *d = dst;
    for (size_t i = 0; i < n / 8; i++) {
        uint64_t w = s[i];
        memcpy(d + i * 8, &w, 8);
    }
}

static void region_zero(void *region, size_t n) {
    volatile uint64_t *d = region;
    for (size_t i = 0; i < n / 8; i++) {
        d[i] = 0;
    }
}

static uint32_t request_crc(const mboot_request_t *r) {
    mboot_request_t hdr = *r;
    hdr.crc32 = 0;
    uint32_t crc = mboot_crc32(0, &hdr, offsetof(mboot_request_t, elems));
    return mboot_crc32(crc, r->elems, r->elems_len);
}

static bool request_valid(const mboot_request_t *r) {
    return r->magic == MBOOT_REQ_MAGIC
           && r->version == MBOOT_REQ_VERSION
           && r->elems_len <= MBOOT_REQ_ELEMS_MAX
           && (r->mode == MBOOT_REQ_DFU || r->mode == MBOOT_REQ_FSLOAD)
           && r->crc32 == request_crc(r);
}

static bool retention_matches(uint32_t w) {
    #if MBOOT_RETENTION_BITS >= 32
    return (w & MBOOT_RET_KEY_MASK) == MBOOT_RET_KEY;
    #else
    uint32_t b = w & 0xFFu;
    return b == MBOOT_RET_8BIT_DFU || b == MBOOT_RET_8BIT_FSLOAD;
    #endif
}

static uint32_t retention_value(mboot_req_mode_t mode) {
    #if MBOOT_RETENTION_BITS >= 32
    return MBOOT_RET_KEY | (mode == MBOOT_REQ_FSLOAD ? MBOOT_RET_FSLOAD : 0);
    #else
    return mode == MBOOT_REQ_FSLOAD ? MBOOT_RET_8BIT_FSLOAD : MBOOT_RET_8BIT_DFU;
    #endif
}

void mboot_request_set_and_reset(mboot_req_mode_t mode, const uint8_t *elems, size_t len) {
    size_t size = 0;
    void *region = mboot_port_request_ram(&size);

    if (mode != MBOOT_REQ_FSLOAD) {
        mode = MBOOT_REQ_DFU;
    }
    if (elems == NULL || len > MBOOT_REQ_ELEMS_MAX) {
        elems = NULL;
        len = 0;
        mode = MBOOT_REQ_DFU;
    }

    // The sequence number follows the update audit log so that it increases from request to
    // request across resets.
    mboot_log_rec_t last;
    uint32_t seq = mboot_updatelog_read(0, &last) == 0 ? last.seq + 1 : 1;

    mboot_request_t req;
    memset(&req, 0, sizeof(req));
    req.magic = MBOOT_REQ_MAGIC;
    req.version = MBOOT_REQ_VERSION;
    req.mode = (uint16_t)mode;
    req.seq = seq;
    req.elems_len = (uint16_t)len;
    if (len != 0) {
        memcpy(req.elems, elems, len);
    }
    req.crc32 = request_crc(&req);

    if (size >= REQUEST_SIZE) {
        region_store(region, &req, REQUEST_SIZE);
    }
    mboot_port_retention_write(retention_value(mode));
    mboot_port_reset();
}

void mboot_request_take(mboot_request_t *out, uint32_t reset_cause) {
    size_t size = 0;
    void *region = mboot_port_request_ram(&size);

    memset(out, 0, sizeof(*out));
    out->mode = MBOOT_REQ_NONE;
    if (size < REGION_SIZE) {
        mboot_port_retention_write(0);
        return;
    }

    if (reset_cause & MBOOT_RESET_POR) {
        // RAM contents are not trusted after power-on: initialise the whole region, which
        // also disarms the fault injection state.
        region_zero(region, REGION_SIZE);
        mboot_port_retention_write(0);
        return;
    }

    mboot_request_t req;
    region_load(&req, region, REQUEST_SIZE);
    if (request_valid(&req)) {
        *out = req;
    } else if (retention_matches(mboot_port_retention_read())) {
        out->mode = MBOOT_REQ_DFU;
    }

    // One-shot: nothing of the request stays behind for a later reset to find.
    mboot_request_clear();
}

void mboot_request_clear(void) {
    size_t size = 0;
    void *region = mboot_port_request_ram(&size);
    if (size >= REQUEST_SIZE) {
        region_zero(region, REQUEST_SIZE);
    }
    mboot_port_retention_write(0);
}
