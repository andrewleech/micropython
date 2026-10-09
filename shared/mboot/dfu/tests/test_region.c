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

// test_region.c - region table handling and alt string generation.
//
// mboot_region_get_alt_string() prepends '@' to the region name, so names in
// the region table do not start with '@'.
//
// Test 15 marks the fake flash as non-writable: mboot_region_init() rejects any
// region that fails mboot_port_flash_is_writable(), which is how protected
// flash is represented.

#include <stdint.h>
#include <string.h>
#include <stdio.h>

#include "mboot_api.h"
#include "mboot_dfu.h"
#include "mboot_region.h"
#include "fake_flash.h"
#include "test_main.h"
#include "test_regions_config.h"

// ---------------------------------------------------------------------------
// Decode a USB string descriptor (UTF-16LE) to ASCII in buf. Returns the
// number of characters written, not counting the NUL.
// ---------------------------------------------------------------------------

static int utf16le_to_ascii(const uint16_t *desc, char *buf, size_t buf_len) {
    if (buf_len == 0) {
        return 0;
    }
    // desc[0] is the header: byte 0 is the total descriptor length, byte 1 the
    // type (0x03).
    const uint8_t *hdr = (const uint8_t *)desc;
    size_t total_bytes = hdr[0];
    size_t content_chars = (total_bytes - 2) / 2;
    const uint16_t *chars = desc + 1; // skip the header
    size_t i;
    for (i = 0; i < content_chars && i < buf_len - 1; ++i) {
        buf[i] = (char)(chars[i] & 0x7F);
    }
    buf[i] = '\0';
    return (int)i;
}

// ---------------------------------------------------------------------------
// Region buffers
// ---------------------------------------------------------------------------

#define REG_BASE (0x60000000u)
#define REG_SECTOR_4K (4096u)
#define REG_SECTOR_64K (65536u)
#define REG_SECTOR_256B (256u)

static uint8_t s_buf_a[REG_SECTOR_64K * 128];
static uint8_t s_buf_b[REG_SECTOR_256B * 16];
static uint8_t s_buf_c[REG_SECTOR_4K * 8];
static fake_flash_t s_ff_a, s_ff_b, s_ff_c;

// ---------------------------------------------------------------------------
// Test 15: mboot_region_init accepts a valid table and rejects a region that
// fails mboot_port_flash_is_writable.
// ---------------------------------------------------------------------------

static int test_15_init_validation(int *failures) {
    fake_flash_reset_registry();
    fake_flash_init(&s_ff_a, REG_BASE, sizeof(s_buf_a), REG_SECTOR_64K, s_buf_a);
    fake_flash_register(&s_ff_a);
    const mboot_region_t valid_regions[] = {
        {
            .addr = REG_BASE,
            .size = sizeof(s_buf_a),
            .sector_size = REG_SECTOR_64K,
            .sector_count = 128,
            .name = "Test Flash",
            .flags = MBOOT_REGION_FLAG_READABLE | MBOOT_REGION_FLAG_ERASE_REQUIRED_BEFORE_WRITE,
        },
    };
    TEST_REGIONS_SET(valid_regions, 1);
    int rc = mboot_region_init();
    TEST_ASSERT_EQ(rc, 0);

    // Not writable.
    s_ff_a.writable = 0;
    rc = mboot_region_init();
    TEST_ASSERT(rc < 0);
    s_ff_a.writable = 1;

    return 0;
}

// ---------------------------------------------------------------------------
// Test 16: Alt string for a 64 KiB x 128 region.
//
// READABLE + ERASE_REQUIRED_BEFORE_WRITE gives perm = 1|6 = 7, which is 'g'
// (DfuSe). Expected: "@Test Flash /0x60000000/128*064Kg"
// ---------------------------------------------------------------------------

static int test_16_alt_string_64k(int *failures) {
    fake_flash_reset_registry();
    fake_flash_init(&s_ff_a, REG_BASE, sizeof(s_buf_a), REG_SECTOR_64K, s_buf_a);
    fake_flash_register(&s_ff_a);
    const mboot_region_t regions[] = {
        {
            .addr = REG_BASE,
            .size = sizeof(s_buf_a),
            .sector_size = REG_SECTOR_64K,
            .sector_count = 128,
            .name = "Test Flash",
            .flags = MBOOT_REGION_FLAG_READABLE | MBOOT_REGION_FLAG_ERASE_REQUIRED_BEFORE_WRITE,
        },
    };
    TEST_REGIONS_SET(regions, 1);
    int rc = mboot_region_init();
    TEST_ASSERT_EQ(rc, 0);

    uint16_t desc[MBOOT_REGION_DESC_MAX_BYTES / 2 + 1];
    int chars = mboot_region_get_alt_string(0, desc, MBOOT_REGION_DESC_MAX_CONTENT_CHARS);
    TEST_ASSERT_GE(chars, 1);

    char ascii[MBOOT_REGION_DESC_MAX_CONTENT_CHARS + 1];
    utf16le_to_ascii(desc, ascii, sizeof(ascii));

    const char *expected = "@Test Flash /0x60000000/128*064Kg";
    if (strcmp(ascii, expected) != 0) {
        printf("  FAIL %s:%d: alt string mismatch\n"
            "    expected: %s\n"
            "    got:      %s\n",
            __FILE__, __LINE__, expected, ascii);
        (*failures)++;
        return -1;
    }
    return 0;
}

// ---------------------------------------------------------------------------
// Test 17: A 256 B x 16 region uses the 'B' unit.
// ---------------------------------------------------------------------------

static int test_17_alt_string_bytes(int *failures) {
    fake_flash_reset_registry();
    fake_flash_init(&s_ff_b, REG_BASE, sizeof(s_buf_b), REG_SECTOR_256B, s_buf_b);
    fake_flash_register(&s_ff_b);
    const mboot_region_t regions[] = {
        {
            .addr = REG_BASE,
            .size = sizeof(s_buf_b),
            .sector_size = REG_SECTOR_256B,
            .sector_count = 16,
            .name = "Small Flash",
            .flags = MBOOT_REGION_FLAG_READABLE,
        },
    };
    TEST_REGIONS_SET(regions, 1);
    int rc = mboot_region_init();
    TEST_ASSERT_EQ(rc, 0);

    uint16_t desc[MBOOT_REGION_DESC_MAX_BYTES / 2 + 1];
    int chars = mboot_region_get_alt_string(0, desc, MBOOT_REGION_DESC_MAX_CONTENT_CHARS);
    TEST_ASSERT_GE(chars, 1);

    char ascii[MBOOT_REGION_DESC_MAX_CONTENT_CHARS + 1];
    utf16le_to_ascii(desc, ascii, sizeof(ascii));

    if (strstr(ascii, "256B") == NULL) {
        printf("  FAIL %s:%d: expected 'B' unit in: %s\n",
            __FILE__, __LINE__, ascii);
        (*failures)++;
        return -1;
    }
    return 0;
}

// ---------------------------------------------------------------------------
// Test 18: mboot_region_set_active(N) with N >= region_count fails.
// ---------------------------------------------------------------------------

static int test_18_set_active_out_of_range(int *failures) {
    fake_flash_reset_registry();
    fake_flash_init(&s_ff_c, REG_BASE,
        (size_t)REG_SECTOR_4K * 8, REG_SECTOR_4K, s_buf_c);
    fake_flash_register(&s_ff_c);
    const mboot_region_t regions[] = {
        {
            .addr = REG_BASE,
            .size = (mboot_addr_t)REG_SECTOR_4K * 8,
            .sector_size = REG_SECTOR_4K,
            .sector_count = 8,
            .name = "Flash",
            .flags = MBOOT_REGION_FLAG_READABLE | MBOOT_REGION_FLAG_ERASE_REQUIRED_BEFORE_WRITE,
        },
    };
    TEST_REGIONS_SET(regions, 1);
    int rc = mboot_region_init();
    TEST_ASSERT_EQ(rc, 0);
    TEST_ASSERT_EQ(mboot_region_count(), (size_t)1);

    // 1 == region_count, out of range.
    rc = mboot_region_set_active(1);
    TEST_ASSERT(rc < 0);

    // Active alt is unchanged.
    TEST_ASSERT_EQ(mboot_region_get_active(), (uint8_t)0);
    return 0;
}

// ---------------------------------------------------------------------------
// Test 19: mboot_region_sector_iter yields sector_count entries in ascending
// address order.
// ---------------------------------------------------------------------------

static int test_19_sector_iter(int *failures) {
    fake_flash_reset_registry();
    fake_flash_init(&s_ff_c, REG_BASE,
        (size_t)REG_SECTOR_4K * 8, REG_SECTOR_4K, s_buf_c);
    fake_flash_register(&s_ff_c);
    const mboot_region_t regions[] = {
        {
            .addr = REG_BASE,
            .size = (mboot_addr_t)REG_SECTOR_4K * 8,
            .sector_size = REG_SECTOR_4K,
            .sector_count = 8,
            .name = "Flash",
            .flags = MBOOT_REGION_FLAG_READABLE | MBOOT_REGION_FLAG_ERASE_REQUIRED_BEFORE_WRITE,
        },
    };
    TEST_REGIONS_SET(regions, 1);
    mboot_region_init();

    uint32_t cookie = 0;
    mboot_addr_t addr, prev_addr = 0;
    uint32_t size;
    unsigned int count = 0;
    int first = 1;
    while (mboot_region_sector_iter(0, &cookie, &addr, &size)) {
        if (!first) {
            TEST_ASSERT(addr > prev_addr);
        }
        prev_addr = addr;
        first = 0;
        count++;
    }
    TEST_ASSERT_EQ(count, 8u);
    return 0;
}

// ---------------------------------------------------------------------------
// Test 20: mboot_region_write rejects an address one byte below the region base.
// ---------------------------------------------------------------------------

static int test_20_write_out_of_range(int *failures) {
    fake_flash_reset_registry();
    fake_flash_init(&s_ff_c, REG_BASE,
        (size_t)REG_SECTOR_4K * 8, REG_SECTOR_4K, s_buf_c);
    fake_flash_register(&s_ff_c);
    const mboot_region_t regions[] = {
        {
            .addr = REG_BASE,
            .size = (mboot_addr_t)REG_SECTOR_4K * 8,
            .sector_size = REG_SECTOR_4K,
            .sector_count = 8,
            .name = "Flash",
            .flags = MBOOT_REGION_FLAG_READABLE | MBOOT_REGION_FLAG_ERASE_REQUIRED_BEFORE_WRITE,
        },
    };
    TEST_REGIONS_SET(regions, 1);
    mboot_region_init();
    mboot_region_set_active(0);

    uint8_t buf[4] = {0};
    int rc = mboot_region_write(REG_BASE - 1, buf, 4);
    TEST_ASSERT(rc < 0);
    return 0;
}

// ---------------------------------------------------------------------------
// Test 36: mboot_region_init rejects a region table with a gap.
// ---------------------------------------------------------------------------

static int test_36_init_gap_rejected(int *failures) {
    fake_flash_reset_registry();
    fake_flash_init(&s_ff_c, REG_BASE,
        (size_t)REG_SECTOR_4K * 8, REG_SECTOR_4K, s_buf_c);
    fake_flash_register(&s_ff_c);

    // Two regions with a gap between them.
    const mboot_region_t gap_regions[] = {
        {
            .addr = REG_BASE,
            .size = (mboot_addr_t)REG_SECTOR_4K * 4,
            .sector_size = REG_SECTOR_4K,
            .sector_count = 4,
            .name = "Flash",
            .flags = MBOOT_REGION_FLAG_READABLE | MBOOT_REGION_FLAG_ERASE_REQUIRED_BEFORE_WRITE,
        },
        {
            // Starts one sector past the end of region 0 (gap of one sector).
            .addr = REG_BASE + (mboot_addr_t)REG_SECTOR_4K * 5,
            .size = (mboot_addr_t)REG_SECTOR_4K * 3,
            .sector_size = REG_SECTOR_4K,
            .sector_count = 3,
            .name = "Flash",
            .flags = MBOOT_REGION_FLAG_READABLE | MBOOT_REGION_FLAG_ERASE_REQUIRED_BEFORE_WRITE,
        },
    };
    TEST_REGIONS_SET(gap_regions, 2);
    int rc = mboot_region_init();
    TEST_ASSERT(rc < 0);
    return 0;
}

// ---------------------------------------------------------------------------
// Test 37: Two adjacent regions with the same name fold into one alt, and the
// alt string has a comma-separated geometry list. Also covers the 'M' (MiB)
// unit and the 3-digit zero padding of values below 10.
// ---------------------------------------------------------------------------

// One backing buffer per region.
static uint8_t s_buf_d[1024 * 1024];       // 1 MiB region
static uint8_t s_buf_e[1024 * 1024 * 2];   // 2 MiB region
static fake_flash_t s_ff_d, s_ff_e;

static int test_37_multi_region_fold(int *failures) {
    fake_flash_reset_registry();
    // 1 MiB then 2 MiB, same name, contiguous.
    fake_flash_init(&s_ff_d, REG_BASE, sizeof(s_buf_d), 1024 * 1024u, s_buf_d);
    fake_flash_register(&s_ff_d);
    mboot_addr_t second_base = REG_BASE + (mboot_addr_t)sizeof(s_buf_d);
    fake_flash_init(&s_ff_e, second_base, sizeof(s_buf_e), 1024 * 1024u, s_buf_e);
    fake_flash_register(&s_ff_e);

    const mboot_region_t regions[] = {
        {
            .addr = REG_BASE,
            .size = sizeof(s_buf_d),
            .sector_size = 1024 * 1024u,
            .sector_count = 1,
            .name = "Big Flash",
            .flags = MBOOT_REGION_FLAG_READABLE | MBOOT_REGION_FLAG_ERASE_REQUIRED_BEFORE_WRITE,
        },
        {
            .addr = second_base,
            .size = sizeof(s_buf_e),
            .sector_size = 1024 * 1024u,
            .sector_count = 2,
            .name = "Big Flash",
            .flags = MBOOT_REGION_FLAG_READABLE | MBOOT_REGION_FLAG_ERASE_REQUIRED_BEFORE_WRITE,
        },
    };
    TEST_REGIONS_SET(regions, 2);
    int rc = mboot_region_init();
    TEST_ASSERT_EQ(rc, 0);
    TEST_ASSERT_EQ(mboot_region_count(), (size_t)1);

    uint16_t desc[MBOOT_REGION_DESC_MAX_BYTES / 2 + 1];
    int chars = mboot_region_get_alt_string(0, desc, MBOOT_REGION_DESC_MAX_CONTENT_CHARS);
    TEST_ASSERT_GE(chars, 1);

    char ascii[MBOOT_REGION_DESC_MAX_CONTENT_CHARS + 1];
    utf16le_to_ascii(desc, ascii, sizeof(ascii));

    // Two geometry runs and the 'M' unit.
    if (strstr(ascii, ",") == NULL) {
        printf("  FAIL %s:%d: expected comma in multi-region alt string: %s\n",
            __FILE__, __LINE__, ascii);
        (*failures)++;
        return -1;
    }
    if (strstr(ascii, "M") == NULL) {
        printf("  FAIL %s:%d: expected 'M' unit in multi-region alt string: %s\n",
            __FILE__, __LINE__, ascii);
        (*failures)++;
        return -1;
    }
    return 0;
}

// ---------------------------------------------------------------------------
// Test 38: mboot_region_read rejects a region that is not readable (-EACCES).
// ---------------------------------------------------------------------------

static int test_38_read_not_readable(int *failures) {
    fake_flash_reset_registry();
    fake_flash_init(&s_ff_c, REG_BASE,
        (size_t)REG_SECTOR_4K * 8, REG_SECTOR_4K, s_buf_c);
    fake_flash_register(&s_ff_c);

    // No READABLE flag.
    const mboot_region_t regions[] = {
        {
            .addr = REG_BASE,
            .size = (mboot_addr_t)REG_SECTOR_4K * 8,
            .sector_size = REG_SECTOR_4K,
            .sector_count = 8,
            .name = "Flash",
            .flags = MBOOT_REGION_FLAG_ERASE_REQUIRED_BEFORE_WRITE,
        },
    };
    TEST_REGIONS_SET(regions, 1);
    mboot_region_init();
    mboot_region_set_active(0);

    uint8_t buf[4];
    int rc = mboot_region_read(REG_BASE, buf, 4);
    TEST_ASSERT(rc < 0);
    return 0;
}

// ---------------------------------------------------------------------------
// Test 39: The regions folded into one alt setting have to fit the DFU
// touched-sector bitmap in total, not just one by one.
// ---------------------------------------------------------------------------

#define CAP_SECTOR (256u)
#define CAP_HALF (MBOOT_DFU_MAX_SECTOR_COUNT / 2)

static uint8_t s_buf_cap_a[CAP_SECTOR * CAP_HALF];
static uint8_t s_buf_cap_b[CAP_SECTOR * (CAP_HALF + 1)];
static fake_flash_t s_ff_cap_a, s_ff_cap_b;

static int cap_init(uint32_t count_b, size_t alts_expected, int *failures) {
    fake_flash_reset_registry();
    fake_flash_init(&s_ff_cap_a, REG_BASE, sizeof(s_buf_cap_a), CAP_SECTOR, s_buf_cap_a);
    fake_flash_register(&s_ff_cap_a);
    mboot_addr_t second_base = REG_BASE + (mboot_addr_t)sizeof(s_buf_cap_a);
    fake_flash_init(&s_ff_cap_b, second_base, count_b * CAP_SECTOR, CAP_SECTOR, s_buf_cap_b);
    fake_flash_register(&s_ff_cap_b);
    const mboot_region_t regions[] = {
        {
            .addr = REG_BASE,
            .size = sizeof(s_buf_cap_a),
            .sector_size = CAP_SECTOR,
            .sector_count = CAP_HALF,
            .name = "Cap Flash",
            .flags = MBOOT_REGION_FLAG_READABLE | MBOOT_REGION_FLAG_ERASE_REQUIRED_BEFORE_WRITE,
        },
        {
            .addr = second_base,
            .size = count_b * CAP_SECTOR,
            .sector_size = CAP_SECTOR,
            .sector_count = count_b,
            .name = "Cap Flash",
            .flags = MBOOT_REGION_FLAG_READABLE | MBOOT_REGION_FLAG_ERASE_REQUIRED_BEFORE_WRITE,
        },
    };
    TEST_REGIONS_SET(regions, 2);
    int rc = mboot_region_init();
    if (rc == 0) {
        TEST_ASSERT_EQ(mboot_region_count(), alts_expected);
    }
    return rc;
}

static int test_39_init_group_sector_total(int *failures) {
    // MBOOT_DFU_MAX_SECTOR_COUNT sectors in one alt setting is accepted.
    int rc = cap_init(CAP_HALF, 1, failures);
    TEST_ASSERT_EQ(rc, 0);

    // One more: each region fits on its own, the alt setting does not.
    rc = cap_init(CAP_HALF + 1, 1, failures);
    TEST_ASSERT(rc < 0);
    return 0;
}

// ---------------------------------------------------------------------------
// Test 40: mboot_region_sector_index numbers the sectors of an alt setting
// across regions with different sector sizes.
// ---------------------------------------------------------------------------

static int test_40_sector_index_mixed(int *failures) {
    fake_flash_reset_registry();
    fake_flash_init(&s_ff_b, REG_BASE, 2048u, REG_SECTOR_256B, s_buf_b);
    fake_flash_register(&s_ff_b);
    // 8 x 256 B followed by 2 x 1 KiB.
    fake_flash_init(&s_ff_c, REG_BASE + 2048u, 2048u, 1024u, s_buf_c);
    fake_flash_register(&s_ff_c);
    const mboot_region_t regions[] = {
        {
            .addr = REG_BASE,
            .size = 2048u,
            .sector_size = REG_SECTOR_256B,
            .sector_count = 8,
            .name = "Mixed",
            .flags = MBOOT_REGION_FLAG_READABLE | MBOOT_REGION_FLAG_ERASE_REQUIRED_BEFORE_WRITE,
        },
        {
            .addr = REG_BASE + 2048u,
            .size = 2048u,
            .sector_size = 1024u,
            .sector_count = 2,
            .name = "Mixed",
            .flags = MBOOT_REGION_FLAG_READABLE | MBOOT_REGION_FLAG_ERASE_REQUIRED_BEFORE_WRITE,
        },
    };
    TEST_REGIONS_SET(regions, 2);
    TEST_ASSERT_EQ(mboot_region_init(), 0);

    uint32_t idx = 0xdeadu;
    TEST_ASSERT(mboot_region_sector_index(0, REG_BASE, &idx));
    TEST_ASSERT_EQ(idx, 0u);
    TEST_ASSERT(mboot_region_sector_index(0, REG_BASE + 255u, &idx));
    TEST_ASSERT_EQ(idx, 0u);
    TEST_ASSERT(mboot_region_sector_index(0, REG_BASE + 7u * 256u + 1u, &idx));
    TEST_ASSERT_EQ(idx, 7u);
    TEST_ASSERT(mboot_region_sector_index(0, REG_BASE + 2048u, &idx));
    TEST_ASSERT_EQ(idx, 8u);
    TEST_ASSERT(mboot_region_sector_index(0, REG_BASE + 2048u + 1024u + 5u, &idx));
    TEST_ASSERT_EQ(idx, 9u);

    // Outside the alt setting, or an unknown alt setting: false, idx unchanged.
    idx = 0xbeefu;
    TEST_ASSERT(!mboot_region_sector_index(0, REG_BASE + 4096u, &idx));
    TEST_ASSERT(!mboot_region_sector_index(0, REG_BASE - 1u, &idx));
    TEST_ASSERT(!mboot_region_sector_index(1, REG_BASE, &idx));
    TEST_ASSERT_EQ(idx, 0xbeefu);
    return 0;
}

// ---------------------------------------------------------------------------
// Entry point
// ---------------------------------------------------------------------------

int test_region(void) {
    int failures = 0;
    RUN_TEST(test_15_init_validation);
    RUN_TEST(test_16_alt_string_64k);
    RUN_TEST(test_17_alt_string_bytes);
    RUN_TEST(test_18_set_active_out_of_range);
    RUN_TEST(test_19_sector_iter);
    RUN_TEST(test_20_write_out_of_range);
    RUN_TEST(test_36_init_gap_rejected);
    RUN_TEST(test_37_multi_region_fold);
    RUN_TEST(test_38_read_not_readable);
    RUN_TEST(test_39_init_group_sector_total);
    RUN_TEST(test_40_sector_index_mixed);
    return failures;
}
