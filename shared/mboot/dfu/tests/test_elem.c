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

// test_elem.c - element stream (TLV) parser edge cases.

#include <stdint.h>
#include <stdbool.h>

#include "mboot_elem.h"
#include "test_main.h"

// ---------------------------------------------------------------------------
// Test 21: Searching an empty buffer returns NULL.
// ---------------------------------------------------------------------------

static int test_21_empty_buffer(int *failures) {
    const uint8_t *p = mboot_elem_search(NULL, 0, MBOOT_ELEM_TYPE_END, NULL);
    TEST_ASSERT_NULL(p);

    // Same for a zero-length buffer that is not NULL.
    uint8_t buf[1] = {0};
    p = mboot_elem_search(buf, 0, MBOOT_ELEM_TYPE_END, NULL);
    TEST_ASSERT_NULL(p);
    return 0;
}

// ---------------------------------------------------------------------------
// Test 22: Searching a stream that is only END returns NULL.
// ---------------------------------------------------------------------------

static int test_22_only_end(int *failures) {
    const uint8_t buf[] = {MBOOT_ELEM_TYPE_END, 0};
    const uint8_t *p = mboot_elem_search(buf, sizeof(buf),
        MBOOT_ELEM_TYPE_FSLOAD, NULL);
    TEST_ASSERT_NULL(p);
    return 0;
}

// ---------------------------------------------------------------------------
// Test 23: A truncated header (buf_len = 1) returns NULL.
// ---------------------------------------------------------------------------

static int test_23_truncated_header(int *failures) {
    const uint8_t buf[1] = {MBOOT_ELEM_TYPE_MOUNT};
    const uint8_t *p = mboot_elem_search(buf, 1, MBOOT_ELEM_TYPE_MOUNT, NULL);
    TEST_ASSERT_NULL(p);
    return 0;
}

// ---------------------------------------------------------------------------
// Test 24: A declared length past the end of the buffer returns NULL.
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
// Test 25: Finds a type in the middle of a well-formed stream.
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
// Test 26: mboot_elem_validate accepts a well-formed stream and rejects a
// missing END or an END with non-zero length.
// ---------------------------------------------------------------------------

static int test_26_validate(int *failures) {
    const uint8_t good[] = {
        MBOOT_ELEM_TYPE_MOUNT, 1, 0xFF,
        MBOOT_ELEM_TYPE_END, 0,
    };
    TEST_ASSERT(mboot_elem_validate(good, sizeof(good)));

    // No END.
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
// Test 27: Type numbers match the stm32 mboot element stream (raw bytes).
// ---------------------------------------------------------------------------

static int test_27_wire_numbers(int *failures) {
    TEST_ASSERT_EQ(MBOOT_ELEM_TYPE_END, 1);
    TEST_ASSERT_EQ(MBOOT_ELEM_TYPE_MOUNT, 2);
    TEST_ASSERT_EQ(MBOOT_ELEM_TYPE_FSLOAD, 3);
    TEST_ASSERT_EQ(MBOOT_ELEM_TYPE_STATUS, 4);

    // A stream as written by ports/stm32/mboot/fwupdate.py: MOUNT, FSLOAD,
    // STATUS, END.
    const uint8_t stream[] = {
        2, 3, 0xA1, 0xA2, 0xA3,
        3, 2, 0xB1, 0xB2,
        4, 4, 0xC1, 0xC2, 0xC3, 0xC4,
        1, 0,
    };
    TEST_ASSERT(mboot_elem_validate(stream, sizeof(stream)));

    uint8_t len = 0;
    const uint8_t *p = mboot_elem_search(stream, sizeof(stream), MBOOT_ELEM_TYPE_MOUNT, &len);
    TEST_ASSERT(p == &stream[2]);
    TEST_ASSERT_EQ(len, 3);
    p = mboot_elem_search(stream, sizeof(stream), MBOOT_ELEM_TYPE_FSLOAD, &len);
    TEST_ASSERT(p == &stream[7]);
    TEST_ASSERT_EQ(len, 2);
    p = mboot_elem_search(stream, sizeof(stream), MBOOT_ELEM_TYPE_STATUS, &len);
    TEST_ASSERT(p == &stream[11]);
    TEST_ASSERT_EQ(len, 4);

    // Type 0 is not a terminator: an all-zero buffer is not a valid stream and
    // a zero-length type 0 element is skipped before the real END.
    const uint8_t zeros[8] = {0};
    TEST_ASSERT(!mboot_elem_validate(zeros, sizeof(zeros)));
    const uint8_t skip_zero[] = {0, 0, 1, 0};
    TEST_ASSERT(mboot_elem_validate(skip_zero, sizeof(skip_zero)));
    return 0;
}

// ---------------------------------------------------------------------------
// Entry point
// ---------------------------------------------------------------------------

int test_elem(void) {
    int failures = 0;
    RUN_TEST(test_21_empty_buffer);
    RUN_TEST(test_22_only_end);
    RUN_TEST(test_23_truncated_header);
    RUN_TEST(test_24_length_overflow);
    RUN_TEST(test_25_search_middle);
    RUN_TEST(test_26_validate);
    RUN_TEST(test_27_wire_numbers);
    return failures;
}
