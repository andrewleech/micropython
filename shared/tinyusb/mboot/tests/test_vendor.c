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

// test_vendor.c — vendor erase opcode (0x80) dispatch tests (items 10-14).

#include <errno.h>
#include <stdint.h>

#include "mboot_api.h"
#include "mboot_dfu.h"
#include "mboot_region.h"
#include "fake_flash.h"
#include "fake_transport.h"
#include "test_main.h"
#include "test_regions_config.h"

// ---------------------------------------------------------------------------
// Region layout: 256 KiB at 0x60000000, 4 KiB sectors (64 sectors).
// ---------------------------------------------------------------------------

#define VND_TEST_BASE (0x60000000u)
#define VND_TEST_SECTOR_SIZE (4096u)
#define VND_TEST_SECTOR_COUNT (64u)
#define VND_TEST_SIZE ((size_t)VND_TEST_SECTOR_SIZE * VND_TEST_SECTOR_COUNT)

static uint8_t s_flash_buf[VND_TEST_SECTOR_SIZE * VND_TEST_SECTOR_COUNT];
static fake_flash_t s_ff;

static const mboot_region_t k_regions[] = {
    {
        .addr = VND_TEST_BASE,
        .size = VND_TEST_SIZE,
        .sector_size = VND_TEST_SECTOR_SIZE,
        .sector_count = VND_TEST_SECTOR_COUNT,
        .name = "Test Flash",
        .flags = MBOOT_REGION_FLAG_READABLE | MBOOT_REGION_FLAG_ERASE_REQUIRED_BEFORE_WRITE,
    },
};

static void setup(void) {
    fake_flash_reset_registry();
    fake_flash_init(&s_ff, VND_TEST_BASE, VND_TEST_SIZE,
        VND_TEST_SECTOR_SIZE, s_flash_buf);
    fake_flash_register(&s_ff);
    TEST_REGIONS_SET(k_regions, 1);
    mboot_region_init();
    mboot_dfu_init();
}

// ---------------------------------------------------------------------------
// Test 10: Vendor 0x80 with addr=0x60000000 len=0x1000 -> 1 page erase call.
// ---------------------------------------------------------------------------

static int test_10_erase_one_sector(int *failures) {
    setup();
    int rc = fake_vendor_erase(0, VND_TEST_BASE, VND_TEST_SECTOR_SIZE);
    TEST_ASSERT_EQ(rc, 0);
    TEST_ASSERT_EQ(s_ff.erase_calls, 1u);
    return 0;
}

// ---------------------------------------------------------------------------
// Test 11: Vendor 0x80 with addr=0x60000000 len=0x10000 (16 sectors) ->
//          16 erase calls.
// ---------------------------------------------------------------------------

static int test_11_erase_sixteen_sectors(int *failures) {
    setup();
    int rc = fake_vendor_erase(0, VND_TEST_BASE, 16u * VND_TEST_SECTOR_SIZE);
    TEST_ASSERT_EQ(rc, 0);
    TEST_ASSERT_EQ(s_ff.erase_calls, 16u);
    return 0;
}

// ---------------------------------------------------------------------------
// Test 12: Vendor 0x80 with len=0xFFFFFFFF (mass erase) -> erases every
//          sector exactly once.
// ---------------------------------------------------------------------------

static int test_12_mass_erase(int *failures) {
    setup();
    int rc = fake_vendor_erase(0, VND_TEST_BASE, 0xFFFFFFFFu);
    TEST_ASSERT_EQ(rc, 0);
    // Total count must equal sector count.
    TEST_ASSERT_EQ(s_ff.erase_calls, VND_TEST_SECTOR_COUNT);
    // Per-sector: each of the 64 sectors must have been erased exactly once.
    for (unsigned int i = 0; i < VND_TEST_SECTOR_COUNT; ++i) {
        if (s_ff.sector_erase_counts[i] != 1u) {
            printf("  FAIL %s:%d: sector %u erase count %u != 1\n",
                __FILE__, __LINE__, i, s_ff.sector_erase_counts[i]);
            (*failures)++;
            return -1;
        }
    }
    return 0;
}

// ---------------------------------------------------------------------------
// Test 13: Vendor 0x80 with addr outside any region -> negative return,
//          zero erase calls.  Tests both lower- and upper-bound cases.
// ---------------------------------------------------------------------------

static int test_13_out_of_range(int *failures) {
    setup();
    // Lower bound: address well below the region base.
    int rc = fake_vendor_erase(0, 0x10000000u, VND_TEST_SECTOR_SIZE);
    TEST_ASSERT(rc < 0);
    TEST_ASSERT_EQ(s_ff.erase_calls, 0u);

    // Upper bound: address exactly at region end (one byte past the last sector).
    mboot_addr_t past_end = VND_TEST_BASE + VND_TEST_SIZE;
    rc = fake_vendor_erase(0, (uint32_t)past_end, VND_TEST_SECTOR_SIZE);
    TEST_ASSERT(rc < 0);
    TEST_ASSERT_EQ(s_ff.erase_calls, 0u);
    return 0;
}

// ---------------------------------------------------------------------------
// Test 14: Vendor request 0x81 (unallocated opcode) returns -EINVAL and has
//          no side effects on DFU state or flash.
// ---------------------------------------------------------------------------

static int test_14_unknown_opcode(int *failures) {
    setup();
    uint8_t payload[8] = {0};
    int rc = mboot_dfu_on_vendor_request(0x81, 0, 0, payload, 8);
    // Must return a specific error, not just "something negative".
    TEST_ASSERT_EQ(rc, -EINVAL);
    // No flash side effects.
    TEST_ASSERT_EQ(s_ff.erase_calls, 0u);
    return 0;
}

// ---------------------------------------------------------------------------
// Test 34: Vendor 0x80 with wValue=1 (non-zero alt) erases the second alt.
//
// Uses two distinct flash devices and a two-region table that maps to two
// separate alt settings (different names).
// ---------------------------------------------------------------------------

// Alt 1 flash starts immediately after alt 0 (contiguous requirement).
#define VND2_BASE (VND_TEST_BASE + VND_TEST_SIZE)
#define VND2_SECTOR_SIZE (4096u)
#define VND2_SECTOR_COUNT (8u)
static uint8_t s_flash_buf2[VND2_SECTOR_SIZE * VND2_SECTOR_COUNT];
static fake_flash_t s_ff2;

static const mboot_region_t k_regions_two_alts[] = {
    {
        .addr = VND_TEST_BASE,
        .size = VND_TEST_SIZE,
        .sector_size = VND_TEST_SECTOR_SIZE,
        .sector_count = VND_TEST_SECTOR_COUNT,
        .name = "Flash A",
        .flags = MBOOT_REGION_FLAG_READABLE | MBOOT_REGION_FLAG_ERASE_REQUIRED_BEFORE_WRITE,
    },
    {
        .addr = VND2_BASE,
        .size = (size_t)VND2_SECTOR_SIZE * VND2_SECTOR_COUNT,
        .sector_size = VND2_SECTOR_SIZE,
        .sector_count = VND2_SECTOR_COUNT,
        .name = "Flash B",
        .flags = MBOOT_REGION_FLAG_READABLE | MBOOT_REGION_FLAG_ERASE_REQUIRED_BEFORE_WRITE,
    },
};

static int test_34_erase_alt_nonzero(int *failures) {
    fake_flash_reset_registry();
    fake_flash_init(&s_ff, VND_TEST_BASE, VND_TEST_SIZE,
        VND_TEST_SECTOR_SIZE, s_flash_buf);
    fake_flash_register(&s_ff);
    fake_flash_init(&s_ff2, VND2_BASE,
        (size_t)VND2_SECTOR_SIZE * VND2_SECTOR_COUNT, VND2_SECTOR_SIZE, s_flash_buf2);
    fake_flash_register(&s_ff2);
    TEST_REGIONS_SET(k_regions_two_alts, 2);
    mboot_region_init();
    mboot_dfu_init();

    // Erase alt 1 (Flash B) using wValue=1.
    int rc = fake_vendor_erase(1, VND2_BASE, VND2_SECTOR_SIZE);
    TEST_ASSERT_EQ(rc, 0);
    // Only Flash B should have erases, not Flash A.
    TEST_ASSERT_EQ(s_ff.erase_calls, 0u);
    TEST_ASSERT_EQ(s_ff2.erase_calls, 1u);
    return 0;
}

// ---------------------------------------------------------------------------
// Test 34b: Vendor 0x80 with wValue >= region count returns -EINVAL.
// ---------------------------------------------------------------------------

static int test_34b_erase_alt_out_of_range(int *failures) {
    setup();
    // With only 1 alt, wValue=1 is out of range.
    int rc = fake_vendor_erase(1, VND_TEST_BASE, VND_TEST_SECTOR_SIZE);
    TEST_ASSERT_EQ(rc, -EINVAL);
    TEST_ASSERT_EQ(s_ff.erase_calls, 0u);
    return 0;
}

// ---------------------------------------------------------------------------
// Test 36: Vendor 0x80 with wrong payload size (not 8 bytes) returns -EINVAL.
// ---------------------------------------------------------------------------

static int test_36_erase_bad_payload(int *failures) {
    setup();
    uint8_t payload[4] = {0};
    int rc = mboot_dfu_on_vendor_request(MBOOT_VREQ_ERASE, 0, 0, payload, 4);
    TEST_ASSERT_EQ(rc, -EINVAL);
    TEST_ASSERT_EQ(s_ff.erase_calls, 0u);
    return 0;
}

// ---------------------------------------------------------------------------
// Test 37: Vendor 0x80 with len=0 is a no-op; no erase calls.
// ---------------------------------------------------------------------------

static int test_37_erase_zero_length(int *failures) {
    setup();
    int rc = fake_vendor_erase(0, VND_TEST_BASE, 0u);
    TEST_ASSERT_EQ(rc, 0);
    TEST_ASSERT_EQ(s_ff.erase_calls, 0u);
    return 0;
}

// ---------------------------------------------------------------------------
// test_vendor entry point
// ---------------------------------------------------------------------------

int test_vendor(void) {
    int failures = 0;
    RUN_TEST(test_10_erase_one_sector);
    RUN_TEST(test_11_erase_sixteen_sectors);
    RUN_TEST(test_12_mass_erase);
    RUN_TEST(test_13_out_of_range);
    RUN_TEST(test_14_unknown_opcode);
    RUN_TEST(test_34_erase_alt_nonzero);
    RUN_TEST(test_34b_erase_alt_out_of_range);
    RUN_TEST(test_36_erase_bad_payload);
    RUN_TEST(test_37_erase_zero_length);
    return failures;
}
