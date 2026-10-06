/*
 * This file is part of the MicroPython project, http://micropython.org/
 *
 * The MIT License (MIT)
 *
 * Copyright (c) 2019-2020 Damien P. George
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

#include <string.h>

#include "mcuboot_layout.h"
#include "mcuboot_fsload.h"

#if defined(MCUBOOT_FSLOAD_GZIP) && MCUBOOT_FSLOAD_GZIP

#include "lib/uzlib/uzlib.h"

// gzip reader for fsload.
//
// The file is decompressed into the caller's buffer as it is read. The output is a pure
// function of the input: the inflate window is cleared whenever decompression (re)starts, so a
// stream that refers to window bytes it hasn't written yet gives the same data on every pass.
// Output is limited to the target capacity. The running count is checked on every call, so an
// input that expands past the limit fails before more than one byte beyond it is produced. uzlib
// checks the trailing CRC-32, this file checks the trailing ISIZE, and a stream that ends inside
// the trailer is an error.
//
// The inflate window is 32 KiB, the largest the gzip format allows, so any compressor setting
// decompresses.

#define GZ_WINDOW_SIZE (1u << 15)
#define GZ_SRC_BUF_SIZE (256u)

typedef struct {
    mcuboot_stream_t base;
    mcuboot_stream_t *src;
    uzlib_uncomp_t d;
    uint32_t limit;
    uint32_t last4;         // the last four source bytes consumed, as a little-endian word
    int err;                // first negative result of the source stream, or 0
    int fail;               // sticky negative result of the last failed call, or 0
    bool done;
    uint16_t src_pos;
    uint16_t src_len;
    uint8_t src_buf[GZ_SRC_BUF_SIZE];
    uint8_t window[GZ_WINDOW_SIZE];
} gz_stream_t;

static gz_stream_t gz_stream;

// Source callback for uzlib. It hands out one byte per call (uzlib's own source/source_limit
// pair is left empty) so that the last four consumed bytes, which are the ISIZE field at the
// end of the stream, are known exactly.
static int gz_source_read_byte(void *data) {
    gz_stream_t *z = data;
    if (z->src_pos == z->src_len) {
        int n = z->src->read(z->src, z->src_buf, sizeof(z->src_buf));
        if (n < 0) {
            z->err = n;
            return -1;
        }
        if (n == 0 || (unsigned)n > sizeof(z->src_buf)) {
            return -1;
        }
        z->src_pos = 0;
        z->src_len = n;
    }
    uint8_t b = z->src_buf[z->src_pos++];
    z->last4 = (z->last4 >> 8) | ((uint32_t)b << 24);
    return b;
}

static int gz_fail(gz_stream_t *z, int code) {
    z->fail = z->err < 0 ? z->err : code;
    return z->fail;
}

// Rewinds the source, parses the gzip header and clears the window.
static int gz_start(gz_stream_t *z) {
    int r = z->src->rewind(z->src);
    if (r < 0) {
        return r;
    }
    memset(&z->d, 0, sizeof(z->d));
    memset(z->window, 0, sizeof(z->window));
    z->d.source_read_data = z;
    z->d.source_read_cb = gz_source_read_byte;
    z->src_pos = 0;
    z->src_len = 0;
    z->last4 = 0;
    z->err = 0;
    z->fail = 0;
    z->done = false;
    z->base.pos = 0;

    int wbits;
    if (uzlib_parse_zlib_gzip_header(&z->d, &wbits) != UZLIB_HEADER_GZIP || z->d.eof) {
        return gz_fail(z, -MCUBOOT_RES_ERR_FS_GZIP);
    }
    uzlib_uncompress_init(&z->d, z->window, GZ_WINDOW_SIZE);
    return 0;
}

static int gz_open(mcuboot_stream_t *s) {
    return gz_start((gz_stream_t *)s);
}

static int gz_rewind(mcuboot_stream_t *s) {
    return gz_start((gz_stream_t *)s);
}

static int gz_read(mcuboot_stream_t *s, uint8_t *dst, uint32_t len) {
    gz_stream_t *z = (gz_stream_t *)s;
    if (z->fail < 0) {
        return z->fail;
    }
    if (len == 0 || z->done) {
        return 0;
    }

    // Ask for one byte more than the limit allows, so that a stream that expands past it is
    // seen. uzlib must never be called with an empty destination.
    uint32_t room = z->limit - s->pos;
    uint32_t want = len;
    if (room < len) {
        want = room + 1;
    }

    z->d.dest = dst;
    z->d.dest_limit = dst + want;
    int st = uzlib_uncompress_chksum(&z->d);
    if (st < 0 || z->err < 0) {
        return gz_fail(z, -MCUBOOT_RES_ERR_FS_GZIP);
    }
    uint32_t n = z->d.dest - dst;
    if (n > room) {
        return gz_fail(z, -MCUBOOT_RES_ERR_FS_GZIP);
    }
    if (st == UZLIB_DONE) {
        // uzlib has consumed the CRC-32 and ISIZE fields. A source that ended inside them
        // reads as zero bytes and sets eof.
        if (z->d.eof || z->last4 != s->pos + n) {
            return gz_fail(z, -MCUBOOT_RES_ERR_FS_GZIP);
        }
        z->done = true;
    }
    s->pos += n;
    return n;
}

int mcuboot_gz_attach(mcuboot_stream_t *src, uint32_t limit, mcuboot_stream_t **out) {
    gz_stream_t *z = &gz_stream;
    z->src = src;
    z->limit = limit;
    z->err = 0;
    z->fail = 0;
    z->done = false;
    z->base.open = gz_open;
    z->base.read = gz_read;
    z->base.rewind = gz_rewind;
    z->base.size = 0;
    z->base.pos = 0;
    *out = &z->base;
    return 0;
}

#endif // MCUBOOT_FSLOAD_GZIP
