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

#ifndef MBOOT_TESTS_TEST_REGIONS_CONFIG_H
#define MBOOT_TESTS_TEST_REGIONS_CONFIG_H

// test_regions_config.h — mutable region table helper for the test binary.
//
// Call test_regions_set() to install a new region configuration before each
// call to mboot_region_init().

#include <stddef.h>

#include "mboot_api.h"

// Maximum number of entries the test region table can hold.
// Must match TEST_REGIONS_MAX in test_regions_config.c.
#define TEST_REGIONS_MAX (8)

// test_regions_set — install a new region table.
//
// Copies count elements from regions[] into the global mboot_port_regions[]
// and updates mboot_port_regions_count.
//
// Pass sizeof(mboot_region_t) as region_size to guard against struct-size
// mismatches between the caller and the definition TU.
void test_regions_set(const void *regions, size_t region_size, size_t count);

// Convenience macro: passes the correct region_size automatically.
#define TEST_REGIONS_SET(arr, count) \
    test_regions_set((arr), sizeof((arr)[0]), (count))

#endif // MBOOT_TESTS_TEST_REGIONS_CONFIG_H
