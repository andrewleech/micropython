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

// test_align.c - MBOOT_DFU_WRITE_ALIGN: DNLOAD blocks are padded with 0xFF up
// to the write unit. Built as its own binary with -DMBOOT_DFU_WRITE_ALIGN=16.

#include <stdint.h>
#include <string.h>

#include "fake_flash.h"
#include "fake_transport.h"
#include "mboot_api.h"
#include "mboot_dfu.h"
#include "mboot_region.h"
#include "test_main.h"
#include "test_regions_config.h"

_Static_assert(MBOOT_DFU_WRITE_ALIGN == 16, "built with -DMBOOT_DFU_WRITE_ALIGN=16");

void test_fail(const char *file, int line, const char *expr) {
    printf("  FAIL %s:%d: %s\n", file, line, expr);
}

int mboot_hook_session_begin(uint8_t alt) {
    (void)alt;
    return 0;
}

#define BASE (0x60000000u)
#define SECT (4096u)

static uint8_t s_buf[SECT * 4];
static fake_flash_t s_ff;
static const mboot_region_t k_regions[] = {
    { .addr = BASE, .size = SECT * 4, .sector_size = SECT, .sector_count = 4, .name = "Flash",
      .flags = MBOOT_REGION_FLAG_READABLE | MBOOT_REGION_FLAG_ERASE_REQUIRED_BEFORE_WRITE },
};

static void setup(void) {
    fake_flash_reset_registry();
    fake_flash_init(&s_ff, BASE, sizeof(s_buf), SECT, s_buf);
    fake_flash_register(&s_ff);
    TEST_REGIONS_SET(k_regions, 1);
    mboot_region_init();
    mboot_dfu_init();
}

static int test_pad_to_write_unit(int *failures) {
    static const struct {
        uint16_t len;
        uint16_t padded;
    } cases[] = { { 1, 16 }, { 3, 16 }, { 16, 16 }, { 17, 32 }, { 100, 112 }, { 2047, 2048 }, { 2048, 2048 } };
    uint8_t payload[2048];
    for (size_t i = 0; i < sizeof(cases) / sizeof(cases[0]); i++) {
        setup();
        memset(payload, 0xA5, sizeof(payload));
        TEST_ASSERT_EQ(fake_dfu_dnload(0, payload, cases[i].len), 0);
        TEST_ASSERT_EQ(s_ff.write_calls, 1u);
        for (unsigned j = 0; j < cases[i].len; j++) {
            TEST_ASSERT_EQ(s_buf[j], 0xA5);
        }
        // The tail up to the next write unit is 0xFF; nothing beyond was written.
        for (unsigned j = cases[i].len; j < cases[i].padded; j++) {
            TEST_ASSERT_EQ(s_buf[j], 0xFF);
        }
        TEST_ASSERT_EQ(s_buf[cases[i].padded % sizeof(s_buf)], 0xFF);
    }
    return 0;
}

// A block that is not a multiple of the unit, then the next block at its
// 2048-aligned address: both writes start on a unit boundary.
static int test_blocks_stay_aligned(int *failures) {
    setup();
    uint8_t payload[2048];
    memset(payload, 0x11, sizeof(payload));
    TEST_ASSERT_EQ(fake_dfu_dnload(0, payload, 100), 0);
    TEST_ASSERT_EQ(fake_dfu_dnload(1, payload, 20), 0);
    TEST_ASSERT_EQ(s_buf[2048], 0x11);
    TEST_ASSERT_EQ(s_buf[2048 + 19], 0x11);
    TEST_ASSERT_EQ(s_buf[2048 + 20], 0xFF);
    TEST_ASSERT_EQ(s_buf[2048 + 31], 0xFF);
    return 0;
}

int main(void) {
    int failures = 0;
    RUN_TEST(test_pad_to_write_unit);
    RUN_TEST(test_blocks_stay_aligned);
    if (failures == 0) {
        printf("ALIGN TESTS PASSED\n");
        return 0;
    }
    printf("ALIGN TOTAL FAILURES: %d\n", failures);
    return 1;
}
