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

// Input: [u8 sel][u8 pad][FAT image]. sel & 7 selects the path, pad the number of erased
// 512 byte sectors after the image inside the window.

int LLVMFuzzerTestOneInput(const uint8_t *data, size_t size) {
    if (size < 3) {
        return 0;
    }
    const char *path = fuzz_paths[data[0] & 7];
    uint32_t pad = data[1];
    const uint8_t *img = data + 2;
    uint32_t img_len = size - 2;

    host_fs_set(img, img_len);
    mboot_fsload_window_t w = {.dev = 0, .base = 0, .len = (img_len + 511) / 512 * 512 + pad * 512};
    mboot_stream_t *s;
    if (mboot_vfs_fat_mount(&w, path, &s) < 0) {
        return 0;
    }
    if (s->open(s) < 0) {
        return 0;
    }
    FUZZ_CHECK(s->pos == 0);

    fuzz_rng_t rng;
    fuzz_rng_init(&rng, data, size);
    uint64_t limit = s->size;
    uint32_t d1, d2;
    int64_t n1 = fuzz_read_all(s, &rng, limit, &d1);

    // A second pass after a rewind returns the same data or the same failure.
    if (s->rewind(s) < 0) {
        return 0;
    }
    FUZZ_CHECK(s->pos == 0);
    int64_t n2 = fuzz_read_all(s, &rng, limit, &d2);
    FUZZ_CHECK((n1 < 0) == (n2 < 0));
    if (n1 >= 0) {
        FUZZ_CHECK(n1 == n2 && d1 == d2);
        FUZZ_CHECK((uint64_t)n1 <= s->size);
    }
    return 0;
}
