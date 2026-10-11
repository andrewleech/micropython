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

#ifndef MICROPY_INCLUDED_TESTS_MBOOT_FUZZ_HARNESS_FUZZ_COMMON_H
#define MICROPY_INCLUDED_TESTS_MBOOT_FUZZ_HARNESS_FUZZ_COMMON_H

#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "mboot_fsload.h"
#include "host_env.h"

// Check that stays on under NDEBUG and aborts, so libFuzzer saves the input.
#define FUZZ_CHECK(cond) \
    do { \
        if (!(cond)) { \
            fprintf(stderr, "%s:%d: check failed: %s\n", __FILE__, __LINE__, #cond); \
            abort(); \
        } \
    } while (0)

// Paths selected by the low three bits of the first input byte of the fat, lfs and run targets.
static const char *const fuzz_paths[8] = {
    "/update.bin",
    "update.bin",
    "/UPDATE.BIN",
    "/dir/update.bin",
    "/a/b/c/update.bin",
    "/firmware.signed.bin",
    "/very long file name for the update image.bin",
    "/",
};

// Block sizes selected by the lfs target.
static const uint32_t fuzz_block_sizes[8] = {0, 128, 256, 512, 1024, 2048, 4096, 8192};

// Deterministic generator seeded from the input, so a run is reproducible.
typedef struct {
    uint32_t state;
} fuzz_rng_t;

static inline void fuzz_rng_init(fuzz_rng_t *r, const uint8_t *data, size_t size) {
    uint32_t h = 2166136261u;
    for (size_t i = 0; i < size; ++i) {
        h = (h ^ data[i]) * 16777619u;
    }
    r->state = h ? h : 1;
}

static inline uint32_t fuzz_rng_next(fuzz_rng_t *r) {
    uint32_t x = r->state;
    x ^= x << 13;
    x ^= x >> 17;
    x ^= x << 5;
    r->state = x;
    return x;
}

static inline uint32_t fuzz_fnv(uint32_t h, const uint8_t *p, size_t n) {
    for (size_t i = 0; i < n; ++i) {
        h = (h ^ p[i]) * 16777619u;
    }
    return h;
}

// Reads s to the end in chunks of 1..4096 bytes. Returns the number of bytes read or a
// negative result, and the FNV hash of the data in *digest. The data must not exceed limit.
static inline int64_t fuzz_read_all(mboot_stream_t *s, fuzz_rng_t *rng, uint64_t limit, uint32_t *digest) {
    static uint8_t buf[4096];
    uint64_t total = 0;
    uint32_t h = 2166136261u;
    for (;;) {
        uint32_t want = 1 + fuzz_rng_next(rng) % sizeof(buf);
        int n = s->read(s, buf, want);
        if (n < 0) {
            *digest = h;
            return n;
        }
        FUZZ_CHECK((uint32_t)n <= want);
        if (n == 0) {
            break;
        }
        total += n;
        FUZZ_CHECK(total <= limit);
        h = fuzz_fnv(h, buf, n);
        FUZZ_CHECK(s->pos == total);
    }
    *digest = h;
    return total;
}

#endif // MICROPY_INCLUDED_TESTS_MBOOT_FUZZ_HARNESS_FUZZ_COMMON_H
