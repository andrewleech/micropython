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

// test_regions_config.c - mutable region table for the test binary.
//
// Implements mboot_port_get_regions(). Tests call test_regions_set() to load
// a region table before each mboot_region_init(); mboot_port_get_regions()
// returns that array.
//
// The array is typed mboot_region_t, so the struct layout is not duplicated
// here. The only write is the memcpy in test_regions_set().

#include <stddef.h>
#include <string.h>

#include "mboot_api.h"

// ---------------------------------------------------------------------------
// Region table storage
// ---------------------------------------------------------------------------

#define TEST_REGIONS_MAX (8)

static mboot_region_t s_test_regions[TEST_REGIONS_MAX];
static size_t s_test_regions_count = 0;

// Returns the table and count last set by test_regions_set().
void mboot_port_get_regions(const mboot_region_t **regions_out, size_t *count_out) {
    *regions_out = s_test_regions;
    *count_out = s_test_regions_count;
}

// Copies count elements from regions[] into s_test_regions[]. region_size
// is the caller's sizeof(mboot_region_t), passed by TEST_REGIONS_SET, to catch
// a struct size mismatch. Counts above TEST_REGIONS_MAX are truncated.
void test_regions_set(const void *regions, size_t region_size, size_t count) {
    if (count > TEST_REGIONS_MAX) {
        count = TEST_REGIONS_MAX;
    }
    memcpy(s_test_regions, regions, count * region_size);
    s_test_regions_count = count;
}
