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

#include "fuzz_common.h"

#include <errno.h>

#include "flash_map_backend/flash_map_backend.h"

#if defined(HAVE_ZLIB)
#include <zlib.h>
#endif

// Input: [u8 mode][u16 data_len][data][plan]. Mode bit 0 wraps the data in gzip (needs zlib).
// The plan is a list of 6 byte records (u32 offset, u16 length). Each record is one
// mboot_stream_area_read() whose result is compared with the data.

#define MAX_DATA (65535u)
#define MAX_PLAN (64u * 6u)
// The largest area is the data plus 15 * 64 bytes of padding.

int LLVMFuzzerTestOneInput(const uint8_t *data, size_t size) {
    static uint8_t gzbuf[MAX_DATA + 1024];
    static uint8_t out[MAX_DATA + 1024 + 1];
    if (size < 3) {
        return 0;
    }
    uint8_t mode = data[0];
    uint32_t data_len = data[1] | (uint32_t)data[2] << 8;
    if (data_len > size - 3) {
        data_len = size - 3;
    }
    const uint8_t *file = data + 3;
    const uint8_t *plan = file + data_len;
    size_t plan_len = size - 3 - data_len;

    const uint8_t *window_data = file;
    uint32_t window_len = data_len;
    bool gz = false;
    if (mode & 1) {
        #if defined(HAVE_ZLIB)
        z_stream zs;
        memset(&zs, 0, sizeof(zs));
        if (deflateInit2(&zs, 6, Z_DEFLATED, 15 + 16, 8, Z_DEFAULT_STRATEGY) != Z_OK) {
            return 0;
        }
        zs.next_in = (Bytef *)(uintptr_t)file;
        zs.avail_in = data_len;
        zs.next_out = gzbuf;
        zs.avail_out = sizeof(gzbuf);
        int zr = deflate(&zs, Z_FINISH);
        window_len = zs.total_out;
        deflateEnd(&zs);
        if (zr != Z_STREAM_END) {
            return 0;
        }
        window_data = gzbuf;
        gz = true;
        #else
        return 0;
        #endif
    }
    if (window_len == 0) {
        return 0;
    }

    host_fs_set(window_data, window_len);
    mboot_fsload_window_t w = {.dev = 0, .base = 0, .len = window_len};
    mboot_stream_t *top;
    FUZZ_CHECK(mboot_vfs_raw_mount(&w, NULL, &top) == 0);
    FUZZ_CHECK(top->open(top) == 0);
    if (gz) {
        mboot_stream_t *src = top;
        FUZZ_CHECK(mboot_gz_attach(src, MAX_DATA, &top) == 0);
        FUZZ_CHECK(top->open(top) == 0);
    }
    // The area may be larger than the file; the bytes after the file read as erased flash.
    uint32_t area_size = data_len + ((mode >> 2) & 0xf) * 64u;
    int r = mboot_fsload_stream_prime(top, !gz, area_size);
    if (data_len == 0 && gz) {
        // An empty file is a valid gzip stream; prime succeeds with size 0.
        FUZZ_CHECK(r == 0 && top->size == 0);
    }
    if (r < 0) {
        return 0;
    }
    FUZZ_CHECK(top->size == data_len);
    FUZZ_CHECK(mboot_fsload_stream_area()->fa_size == area_size);
    FUZZ_CHECK(mboot_stream_area_read(0, out, 0) == 0);
    FUZZ_CHECK(mboot_stream_area_read(area_size + 1, out, 0) == -ERANGE);

    // Only the first records of the plan are applied; a long plan just makes a run slower.
    if (plan_len > MAX_PLAN) {
        plan_len = MAX_PLAN;
    }
    for (size_t i = 0; i + 6 <= plan_len; i += 6) {
        uint32_t off = plan[i] | (uint32_t)plan[i + 1] << 8 | (uint32_t)plan[i + 2] << 16 | (uint32_t)plan[i + 3] << 24;
        uint32_t len = plan[i + 4] | (uint32_t)plan[i + 5] << 8;
        if ((i / 6) % 7 == 6) {
            // Out of range: the request must be refused, whatever the offset.
            uint32_t bad_off = area_size + 1 + off % 100;
            FUZZ_CHECK(mboot_stream_area_read(bad_off, out, 1) == -ERANGE);
            FUZZ_CHECK(mboot_stream_area_read(off % (area_size + 1), out, area_size + 1 + len) == -ERANGE);
            continue;
        }
        off %= area_size + 1;
        len %= area_size - off + 1;
        FUZZ_CHECK(mboot_stream_area_read(off, out, len) == 0);
        for (uint32_t k = 0; k < len; ++k) {
            FUZZ_CHECK(out[k] == (off + k < data_len ? file[off + k] : 0xff));
        }
    }
    return 0;
}
