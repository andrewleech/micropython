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

// test_dfu.c - DFU download flash dispatch tests.
//
// TinyUSB runs the DFU state machine on the wire. These tests cover the
// per-block flash and bitmap dispatch in mboot_dfu.c: one erase per sector,
// padding, bitmap reset on SET_INTERFACE / ABORT, end-of-transfer marker and
// rejection of oversized blocks.

#include <stdint.h>
#include <string.h>

#include "mboot_api.h"
#include "mboot_dfu.h"
#include "mboot_region.h"
#include "fake_flash.h"
#include "fake_transport.h"
#include "test_main.h"
#include "test_regions_config.h"

// ---------------------------------------------------------------------------
// Region layout
// ---------------------------------------------------------------------------

#define DFU_TEST_BASE (0x60000000u)
#define DFU_TEST_SECTOR_SIZE (4096u)
#define DFU_TEST_SECTOR_COUNT (16u)

static uint8_t s_flash_buf[DFU_TEST_SECTOR_SIZE * DFU_TEST_SECTOR_COUNT];
static fake_flash_t s_ff;

static const mboot_region_t k_regions[] = {
    {
        .addr = DFU_TEST_BASE,
        .size = (mboot_addr_t)DFU_TEST_SECTOR_SIZE * DFU_TEST_SECTOR_COUNT,
        .sector_size = DFU_TEST_SECTOR_SIZE,
        .sector_count = DFU_TEST_SECTOR_COUNT,
        .name = "Test Flash",
        .flags = MBOOT_REGION_FLAG_READABLE | MBOOT_REGION_FLAG_ERASE_REQUIRED_BEFORE_WRITE,
    },
};

// ---------------------------------------------------------------------------
// Setup
// ---------------------------------------------------------------------------

static void setup(void) {
    fake_flash_reset_registry();
    fake_flash_init(&s_ff, DFU_TEST_BASE,
        (size_t)DFU_TEST_SECTOR_SIZE * DFU_TEST_SECTOR_COUNT,
        DFU_TEST_SECTOR_SIZE, s_flash_buf);
    fake_flash_register(&s_ff);
    TEST_REGIONS_SET(k_regions, 1);
    mboot_region_init();
    mboot_dfu_init();
}

// ---------------------------------------------------------------------------
// Test 1: Zero-length DNLOAD returns 0 with no flash side effect.
// ---------------------------------------------------------------------------

static int test_1_dnload_eot(int *failures) {
    setup();
    int rc = fake_dfu_dnload(0, NULL, 0);
    TEST_ASSERT_EQ(rc, 0);
    TEST_ASSERT_EQ(s_ff.erase_calls, 0u);
    TEST_ASSERT_EQ(s_ff.write_calls, 0u);
    return 0;
}

// ---------------------------------------------------------------------------
// Test 2: DNLOAD wLength > MBOOT_DFU_XFER_SIZE is rejected.
// ---------------------------------------------------------------------------

static int test_2_dnload_too_large(int *failures) {
    setup();
    uint8_t payload[4] = {0};
    int rc = fake_dfu_dnload(0, payload, (uint16_t)(MBOOT_DFU_XFER_SIZE + 1));
    TEST_ASSERT(rc < 0);
    TEST_ASSERT_EQ(s_ff.erase_calls, 0u);
    TEST_ASSERT_EQ(s_ff.write_calls, 0u);
    return 0;
}

// ---------------------------------------------------------------------------
// Test 3: DNLOAD with non-multiple-of-4 length pads the tail with 0xFF.
// ---------------------------------------------------------------------------

static int test_3_dnload_padding(int *failures) {
    setup();
    uint8_t payload[3] = {0xAA, 0xBB, 0xCC};
    int rc = fake_dfu_dnload(0, payload, 3);
    TEST_ASSERT_EQ(rc, 0);
    uint8_t readback[4];
    mboot_port_flash_read(DFU_TEST_BASE, readback, 4);
    TEST_ASSERT_EQ(readback[0], 0xAA);
    TEST_ASSERT_EQ(readback[1], 0xBB);
    TEST_ASSERT_EQ(readback[2], 0xCC);
    TEST_ASSERT_EQ(readback[3], 0xFF);
    return 0;
}

// ---------------------------------------------------------------------------
// Test 4: Each sector is erased once, on first touch.
//   sector_size = 4096, MBOOT_DFU_XFER_SIZE = 2048
//   Blocks 0,1 land in sector 0 (offsets 0, 2048).
//   Blocks 2,3 land in sector 1 (offsets 4096, 6144).
//   4 writes across 2 sectors -> 2 erase calls.
// ---------------------------------------------------------------------------

static int test_4_implicit_erase(int *failures) {
    setup();
    uint8_t payload[4] = {0x01, 0x02, 0x03, 0x04};

    // Block 0, sector 0: first touch, erases.
    fake_dfu_dnload(0, payload, 4);
    TEST_ASSERT_EQ(s_ff.erase_calls, 1u);

    // Block 1, sector 0: already erased.
    fake_dfu_dnload(1, payload, 4);
    TEST_ASSERT_EQ(s_ff.erase_calls, 1u);

    // Block 2, sector 1: first touch, erases.
    fake_dfu_dnload(2, payload, 4);
    TEST_ASSERT_EQ(s_ff.erase_calls, 2u);

    // Block 3, sector 1: already erased.
    fake_dfu_dnload(3, payload, 4);
    TEST_ASSERT_EQ(s_ff.erase_calls, 2u);

    return 0;
}

// ---------------------------------------------------------------------------
// Test 5: mboot_dfu_notify_set_interface clears the bitmap and sets the alt.
//
// Called for both SET_INTERFACE and DFU_ABORT. A write to the same sector
// after the notify must erase it again.
// ---------------------------------------------------------------------------

static int test_5_notify_set_interface(int *failures) {
    setup();
    uint8_t payload[4] = {1, 2, 3, 4};

    fake_dfu_dnload(0, payload, 4);
    TEST_ASSERT_EQ(s_ff.erase_calls, 1u);

    // As for SET_INTERFACE or DFU_ABORT.
    mboot_dfu_notify_set_interface(0);

    fake_dfu_dnload(0, payload, 4);
    TEST_ASSERT_EQ(s_ff.erase_calls, 2u);
    return 0;
}

// ---------------------------------------------------------------------------
// Tests 6 and 7: one alt setting made of two regions with different sector
// sizes (4 x 4 KiB followed by 2 x 8 KiB). The touched-sector bitmap has one
// bit per sector of the alt setting, so sectors in the second region never
// share a bit with sectors in the first.
// ---------------------------------------------------------------------------

#define MIX_SECTOR_A (4096u)
#define MIX_SECTOR_B (8192u)
#define MIX_SIZE_A (4u * MIX_SECTOR_A)
#define MIX_SIZE_B (2u * MIX_SECTOR_B)

static uint8_t s_mix_buf_a[MIX_SIZE_A];
static uint8_t s_mix_buf_b[MIX_SIZE_B];
static fake_flash_t s_mix_ff_a, s_mix_ff_b;

static const mboot_region_t k_mix_regions[] = {
    {
        .addr = DFU_TEST_BASE,
        .size = MIX_SIZE_A,
        .sector_size = MIX_SECTOR_A,
        .sector_count = 4,
        .name = "Mixed",
        .flags = MBOOT_REGION_FLAG_READABLE | MBOOT_REGION_FLAG_ERASE_REQUIRED_BEFORE_WRITE,
    },
    {
        .addr = DFU_TEST_BASE + MIX_SIZE_A,
        .size = MIX_SIZE_B,
        .sector_size = MIX_SECTOR_B,
        .sector_count = 2,
        .name = "Mixed",
        .flags = MBOOT_REGION_FLAG_READABLE | MBOOT_REGION_FLAG_ERASE_REQUIRED_BEFORE_WRITE,
    },
};

// DNLOAD block numbers (MBOOT_DFU_XFER_SIZE = 2048) of the first byte of the
// third sector of region A (offset 8 KiB) and of the two sectors of region B.
#define MIX_BLOCK_A2 (MIX_SECTOR_A * 2u / MBOOT_DFU_XFER_SIZE)
#define MIX_BLOCK_B0 (MIX_SIZE_A / MBOOT_DFU_XFER_SIZE)
#define MIX_BLOCK_B1 ((MIX_SIZE_A + MIX_SECTOR_B) / MBOOT_DFU_XFER_SIZE)

static int mix_setup(void) {
    fake_flash_reset_registry();
    fake_flash_init(&s_mix_ff_a, DFU_TEST_BASE, MIX_SIZE_A, MIX_SECTOR_A, s_mix_buf_a);
    fake_flash_register(&s_mix_ff_a);
    fake_flash_init(&s_mix_ff_b, DFU_TEST_BASE + MIX_SIZE_A, MIX_SIZE_B, MIX_SECTOR_B,
        s_mix_buf_b);
    fake_flash_register(&s_mix_ff_b);
    TEST_REGIONS_SET(k_mix_regions, 2);
    int rc = mboot_region_init();
    mboot_dfu_init();
    return rc;
}

static int test_6_mixed_sector_sizes_dnload(int *failures) {
    TEST_ASSERT_EQ(mix_setup(), 0);
    TEST_ASSERT_EQ(mboot_region_count(), (size_t)1);
    uint8_t payload[4] = {1, 2, 3, 4};

    // Third sector of region A.
    TEST_ASSERT_EQ(fake_dfu_dnload(MIX_BLOCK_A2, payload, 4), 0);
    TEST_ASSERT_EQ(s_mix_ff_a.erase_calls, 1u);

    // First sector of region B must be erased even though region A sector 2
    // is already touched.
    TEST_ASSERT_EQ(fake_dfu_dnload(MIX_BLOCK_B0, payload, 4), 0);
    TEST_ASSERT_EQ(s_mix_ff_b.erase_calls, 1u);

    // Second block of the same sector: no new erase.
    TEST_ASSERT_EQ(fake_dfu_dnload(MIX_BLOCK_B0 + 1, payload, 4), 0);
    TEST_ASSERT_EQ(s_mix_ff_b.erase_calls, 1u);

    // Second sector of region B.
    TEST_ASSERT_EQ(fake_dfu_dnload(MIX_BLOCK_B1, payload, 4), 0);
    TEST_ASSERT_EQ(s_mix_ff_b.erase_calls, 2u);
    TEST_ASSERT_EQ(s_mix_ff_a.erase_calls, 1u);
    return 0;
}

static int test_7_mixed_sector_sizes_range_erase(int *failures) {
    TEST_ASSERT_EQ(mix_setup(), 0);
    uint8_t payload[4] = {1, 2, 3, 4};

    // Range erase of the third sector of region A marks it touched.
    TEST_ASSERT_EQ(fake_vendor_erase(0, DFU_TEST_BASE + 2u * MIX_SECTOR_A, MIX_SECTOR_A), 0);
    TEST_ASSERT_EQ(s_mix_ff_a.erase_calls, 1u);

    // The following write to region A does not erase again.
    TEST_ASSERT_EQ(fake_dfu_dnload(MIX_BLOCK_A2, payload, 4), 0);
    TEST_ASSERT_EQ(s_mix_ff_a.erase_calls, 1u);

    // A write to the first sector of region B still erases it.
    TEST_ASSERT_EQ(fake_dfu_dnload(MIX_BLOCK_B0, payload, 4), 0);
    TEST_ASSERT_EQ(s_mix_ff_b.erase_calls, 1u);
    return 0;
}

// ---------------------------------------------------------------------------
// Entry point
// ---------------------------------------------------------------------------

int test_dfu(void) {
    int failures = 0;
    RUN_TEST(test_1_dnload_eot);
    RUN_TEST(test_2_dnload_too_large);
    RUN_TEST(test_3_dnload_padding);
    RUN_TEST(test_4_implicit_erase);
    RUN_TEST(test_5_notify_set_interface);
    RUN_TEST(test_6_mixed_sector_sizes_dnload);
    RUN_TEST(test_7_mixed_sector_sizes_range_erase);
    return failures;
}
