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

// test_elem.c — TLV parser edge cases (items 21-26).

#include <stdint.h>
#include <stdbool.h>

#include "mboot_elem.h"
#include "test_main.h"

// ---------------------------------------------------------------------------
// Test 21: Search for END on an empty buffer returns NULL.
// ---------------------------------------------------------------------------

static int test_21_empty_buffer(int *failures) {
    const uint8_t *p = mboot_elem_search(NULL, 0, MBOOT_ELEM_TYPE_END, NULL);
    TEST_ASSERT_NULL(p);

    // Zero-length non-NULL buffer also returns NULL.
    uint8_t buf[1] = {0};
    p = mboot_elem_search(buf, 0, MBOOT_ELEM_TYPE_END, NULL);
    TEST_ASSERT_NULL(p);
    return 0;
}

// ---------------------------------------------------------------------------
// Test 22: Search for type X in a stream with only END returns NULL.
// ---------------------------------------------------------------------------

static int test_22_only_end(int *failures) {
    // Well-formed stream: just END with length 0.
    const uint8_t buf[] = {MBOOT_ELEM_TYPE_END, 0};
    const uint8_t *p = mboot_elem_search(buf, sizeof(buf),
        MBOOT_ELEM_TYPE_FSLOAD, NULL);
    TEST_ASSERT_NULL(p);
    return 0;
}

// ---------------------------------------------------------------------------
// Test 23: Search with a truncated header (buf_len = 1) returns NULL safely.
// ---------------------------------------------------------------------------

static int test_23_truncated_header(int *failures) {
    const uint8_t buf[1] = {MBOOT_ELEM_TYPE_MOUNT};
    const uint8_t *p = mboot_elem_search(buf, 1, MBOOT_ELEM_TYPE_MOUNT, NULL);
    TEST_ASSERT_NULL(p);
    return 0;
}

// ---------------------------------------------------------------------------
// Test 24: Search with declared length exceeding remaining buffer returns NULL.
// ---------------------------------------------------------------------------

static int test_24_length_overflow(int *failures) {
    // type=MOUNT, length=10, but only 3 bytes total.
    const uint8_t buf[] = {MBOOT_ELEM_TYPE_MOUNT, 10, 0x00};
    const uint8_t *p = mboot_elem_search(buf, sizeof(buf),
        MBOOT_ELEM_TYPE_MOUNT, NULL);
    TEST_ASSERT_NULL(p);
    return 0;
}

// ---------------------------------------------------------------------------
// Test 25: Search succeeds on a well-formed stream with target type in middle.
// ---------------------------------------------------------------------------

static int test_25_search_middle(int *failures) {
    // Stream: MOUNT(2 bytes payload), FSLOAD(3 bytes payload), END.
    const uint8_t buf[] = {
        MBOOT_ELEM_TYPE_MOUNT, 2, 0xAA, 0xBB,
        MBOOT_ELEM_TYPE_FSLOAD, 3, 0x11, 0x22, 0x33,
        MBOOT_ELEM_TYPE_END, 0,
    };
    uint8_t out_len = 0;
    const uint8_t *p = mboot_elem_search(buf, sizeof(buf),
        MBOOT_ELEM_TYPE_FSLOAD, &out_len);
    TEST_ASSERT_NOTNULL(p);
    TEST_ASSERT_EQ(out_len, 3);
    TEST_ASSERT_EQ(p[0], 0x11);
    TEST_ASSERT_EQ(p[1], 0x22);
    TEST_ASSERT_EQ(p[2], 0x33);
    return 0;
}

// ---------------------------------------------------------------------------
// Test 26: mboot_elem_validate returns true on well-formed stream;
//          false on missing END; false on END with non-zero length.
// ---------------------------------------------------------------------------

static int test_26_validate(int *failures) {
    // Well-formed stream.
    const uint8_t good[] = {
        MBOOT_ELEM_TYPE_MOUNT, 1, 0xFF,
        MBOOT_ELEM_TYPE_END, 0,
    };
    TEST_ASSERT(mboot_elem_validate(good, sizeof(good)));

    // Missing END (stream ends at last payload byte without END).
    const uint8_t no_end[] = {
        MBOOT_ELEM_TYPE_MOUNT, 1, 0xFF,
    };
    TEST_ASSERT(!mboot_elem_validate(no_end, sizeof(no_end)));

    // END with non-zero length.
    const uint8_t bad_end[] = {
        MBOOT_ELEM_TYPE_END, 1, 0xFF,
    };
    TEST_ASSERT(!mboot_elem_validate(bad_end, sizeof(bad_end)));

    return 0;
}

// ---------------------------------------------------------------------------
// test_elem entry point
// ---------------------------------------------------------------------------

int test_elem(void) {
    int failures = 0;
    RUN_TEST(test_21_empty_buffer);
    RUN_TEST(test_22_only_end);
    RUN_TEST(test_23_truncated_header);
    RUN_TEST(test_24_length_overflow);
    RUN_TEST(test_25_search_middle);
    RUN_TEST(test_26_validate);
    return failures;
}
