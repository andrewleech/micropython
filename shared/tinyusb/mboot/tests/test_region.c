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

// test_region.c — region table behaviour + alt-string generation (items 15-20).
//
// Notes on deviations from the phase file spec:
//
//   Region name convention: mboot_region_get_alt_string() prepends '@' to the
//   region name.  Region names in the region table must therefore NOT include a
//   leading '@'.  The phase file examples showed names without '@'; tests here
//   follow that convention.
//
//   Test 15: the phase spec says "overlap against protected flash returns
//   -EINVAL".  The actual mboot_region_init() check rejects any region that
//   fails mboot_port_flash_is_writable() — that is how protected flash is
//   represented.  This test exercises that path by marking the fake flash
//   device as non-writable.

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
// Helper: decode a USB string descriptor (UTF-16LE) to ASCII into buf.
// Returns the number of characters written (not counting NUL).
// ---------------------------------------------------------------------------

static int utf16le_to_ascii(const uint16_t *desc, char *buf, size_t buf_len) {
    if (buf_len == 0) {
        return 0;
    }
    // desc[0] holds the two-byte header packed as uint16 LE: byte 0 is the
    // total descriptor length; byte 1 is type 0x03.
    const uint8_t *hdr = (const uint8_t *)desc;
    size_t total_bytes = hdr[0];
    size_t content_chars = (total_bytes - 2) / 2;
    const uint16_t *chars = desc + 1; // skip header uint16
    size_t i;
    for (i = 0; i < content_chars && i < buf_len - 1; ++i) {
        buf[i] = (char)(chars[i] & 0x7F);
    }
    buf[i] = '\0';
    return (int)i;
}

// ---------------------------------------------------------------------------
// Shared region buffers
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
// Test 15: mboot_region_init with a valid table returns 0;
//          a region that fails mboot_port_flash_is_writable returns -EINVAL.
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

    // Mark the flash as non-writable; init must reject it.
    s_ff_a.writable = 0;
    rc = mboot_region_init();
    TEST_ASSERT(rc < 0);
    s_ff_a.writable = 1;

    return 0;
}

// ---------------------------------------------------------------------------
// Test 16: alt string for 64 KiB x 128 region.
//
// READABLE + ERASE_REQUIRED_BEFORE_WRITE -> perm = 1|6 = 7 -> 'a'+7 = 'h'.
// Expected output: "@Test Flash /0x60000000/128*064Kh"
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

    const char *expected = "@Test Flash /0x60000000/128*064Kh";
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
// Test 17: alt string for 256 B x 16 region uses the 'B' unit.
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

    // Verify the 'B' unit appears in the output.
    if (strstr(ascii, "256B") == NULL) {
        printf("  FAIL %s:%d: expected 'B' unit in: %s\n",
            __FILE__, __LINE__, ascii);
        (*failures)++;
        return -1;
    }
    return 0;
}

// ---------------------------------------------------------------------------
// Test 18: mboot_region_set_active(N) where N >= region_count returns error.
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

    // N == region_count (1) is out of range.
    rc = mboot_region_set_active(1);
    TEST_ASSERT(rc < 0);

    // Active alt must remain 0.
    TEST_ASSERT_EQ(mboot_region_get_active(), (uint8_t)0);
    return 0;
}

// ---------------------------------------------------------------------------
// Test 19: mboot_region_sector_iter yields exactly sector_count entries
//          in monotonically ascending address order.
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
// Test 20: mboot_region_write rejects an address one byte before region base.
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
    // Address one byte before base: must be rejected.
    int rc = mboot_region_write(REG_BASE - 1, buf, 4);
    TEST_ASSERT(rc < 0);
    return 0;
}

// ---------------------------------------------------------------------------
// Test 36: mboot_region_init rejects out-of-order (gap) region table.
// ---------------------------------------------------------------------------

static int test_36_init_gap_rejected(int *failures) {
    fake_flash_reset_registry();
    fake_flash_init(&s_ff_c, REG_BASE,
        (size_t)REG_SECTOR_4K * 8, REG_SECTOR_4K, s_buf_c);
    fake_flash_register(&s_ff_c);

    // Two regions with a gap between them: second starts at base + 2*size.
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
// Test 37: Two same-name adjacent regions fold into one alt; alt string has
//          a comma-separated geometry list.  Also exercises the 'M' (MiB) unit
//          path and the 3-digit zero-padding for sub-10 values.
// ---------------------------------------------------------------------------

// We need two backing buffers, one for each region half.
static uint8_t s_buf_d[1024 * 1024];       // 1 MiB region
static uint8_t s_buf_e[1024 * 1024 * 2];   // 2 MiB region
static fake_flash_t s_ff_d, s_ff_e;

static int test_37_multi_region_fold(int *failures) {
    fake_flash_reset_registry();
    // 1 MiB region then 2 MiB region, same name, contiguous.
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
    // Two same-name adjacent regions must fold to one alt.
    TEST_ASSERT_EQ(mboot_region_count(), (size_t)1);

    uint16_t desc[MBOOT_REGION_DESC_MAX_BYTES / 2 + 1];
    int chars = mboot_region_get_alt_string(0, desc, MBOOT_REGION_DESC_MAX_CONTENT_CHARS);
    TEST_ASSERT_GE(chars, 1);

    char ascii[MBOOT_REGION_DESC_MAX_CONTENT_CHARS + 1];
    utf16le_to_ascii(desc, ascii, sizeof(ascii));

    // Must contain a comma (two geometry runs) and 'M' unit.
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
// Test 38: mboot_region_read rejects a non-readable region (-EACCES).
// ---------------------------------------------------------------------------

static int test_38_read_not_readable(int *failures) {
    fake_flash_reset_registry();
    fake_flash_init(&s_ff_c, REG_BASE,
        (size_t)REG_SECTOR_4K * 8, REG_SECTOR_4K, s_buf_c);
    fake_flash_register(&s_ff_c);

    // Region without READABLE flag.
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
// test_region entry point
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
    return failures;
}
