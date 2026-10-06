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

#if defined(HAVE_ZLIB)
#include <zlib.h>
#endif

// Input: [u8 limit_log2][gzip bytes]. The decompressed size limit is 1 << (limit_log2 % 24 + 4),
// capped at 256 KiB (a bigger limit only makes expanding inputs slower to run).
//
// The gzip reader runs over a raw window holding the input, twice (the second time after a
// rewind), and must give the same data or the same kind of failure both times. With zlib, an
// input that zlib inflates completely within the limit must also be accepted, with identical
// output.

int LLVMFuzzerTestOneInput(const uint8_t *data, size_t size) {
    if (size < 2) {
        return 0;
    }
    uint32_t limit = 1u << (data[0] % 24 + 4);
    if (limit > (256u << 10)) {
        limit = 256u << 10;
    }
    const uint8_t *gz = data + 1;
    uint32_t gz_len = size - 1;

    host_fs_set(gz, gz_len);
    mcuboot_fsload_window_t w = {.dev = 0, .base = 0, .len = gz_len};
    mcuboot_stream_t *src, *top;
    FUZZ_CHECK(mcuboot_vfs_raw_mount(&w, NULL, &src) == 0);
    FUZZ_CHECK(src->open(src) == 0);
    FUZZ_CHECK(mcuboot_gz_attach(src, limit, &top) == 0);
    if (top->open(top) < 0) {
        return 0;
    }

    fuzz_rng_t rng;
    fuzz_rng_init(&rng, data, size);
    uint32_t d1, d2;
    int64_t n1 = fuzz_read_all(top, &rng, limit, &d1);
    if (n1 >= 0) {
        FUZZ_CHECK(top->pos == (uint64_t)n1);
    }

    FUZZ_CHECK(top->rewind(top) < 0 || top->pos == 0);
    int64_t n2 = fuzz_read_all(top, &rng, limit, &d2);
    FUZZ_CHECK((n1 < 0) == (n2 < 0));
    if (n1 >= 0) {
        FUZZ_CHECK(n1 == n2 && d1 == d2);
    }

    #if defined(HAVE_ZLIB)
    {
        static uint8_t out[(256u << 10) + 1];
        z_stream zs;
        memset(&zs, 0, sizeof(zs));
        if (inflateInit2(&zs, 15 + 16) == Z_OK) {
            zs.next_in = (Bytef *)(uintptr_t)gz;
            zs.avail_in = gz_len;
            zs.next_out = out;
            zs.avail_out = limit + 1;
            int zr = inflate(&zs, Z_FINISH);
            if (zr == Z_STREAM_END && zs.total_out <= limit) {
                uint32_t dz = fuzz_fnv(2166136261u, out, zs.total_out);
                FUZZ_CHECK(n1 >= 0);
                FUZZ_CHECK((uint64_t)n1 == zs.total_out);
                FUZZ_CHECK(d1 == dz);
            }
            inflateEnd(&zs);
        }
    }
    #endif
    return 0;
}
