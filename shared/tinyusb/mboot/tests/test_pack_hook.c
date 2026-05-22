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

// test_pack_hook.c — Test 9: compile-time hook verification for MBOOT_ENABLE_PACKING.
//
// Built as a separate binary (test_runner_pack) with -DMBOOT_ENABLE_PACKING=1.
// Provides a stub mboot_pack_write() that records calls, then verifies that a
// DFU_DNLOAD reaches the stub instead of mboot_region_write().
//
// This file provides: main(), stub mboot_pack_write(), and the mboot_port_*
// callbacks.  It reuses fake_flash.c for flash backend symbols.

#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include "mboot_api.h"
#include "mboot_dfu.h"
#include "mboot_region.h"
#include "mboot_pack.h"
#include "fake_flash.h"
#include "fake_transport.h"
#include "test_main.h"
#include "test_regions_config.h"

// ---------------------------------------------------------------------------
// Stub mboot_pack_write: records whether it was called.
// ---------------------------------------------------------------------------

static int s_pack_write_calls = 0;
static mboot_addr_t s_pack_write_last_addr = 0;

int mboot_pack_write(mboot_addr_t addr, const uint8_t *src, size_t len, bool dry_run) {
    (void)src;
    (void)len;
    (void)dry_run;
    s_pack_write_calls++;
    s_pack_write_last_addr = addr;
    // Return a sentinel error so the DFU layer sees a non-zero result (we do
    // not want to commit to flash; the test only checks the hook is reached).
    return -42;
}

// ---------------------------------------------------------------------------
// Region layout
// ---------------------------------------------------------------------------

#define PACK_TEST_BASE (0x60000000u)
#define PACK_TEST_SECTOR_SIZE (4096u)
#define PACK_TEST_SECTOR_COUNT (16u)

static uint8_t s_flash_buf[PACK_TEST_SECTOR_SIZE * PACK_TEST_SECTOR_COUNT];
static fake_flash_t s_ff;

static const mboot_region_t k_regions[] = {
    {
        .addr = PACK_TEST_BASE,
        .size = (mboot_addr_t)PACK_TEST_SECTOR_SIZE * PACK_TEST_SECTOR_COUNT,
        .sector_size = PACK_TEST_SECTOR_SIZE,
        .sector_count = PACK_TEST_SECTOR_COUNT,
        .name = "Pack Test Flash",
        .flags = MBOOT_REGION_FLAG_READABLE | MBOOT_REGION_FLAG_ERASE_REQUIRED_BEFORE_WRITE,
    },
};

// ---------------------------------------------------------------------------
// Test 9: DFU_DNLOAD reaches mboot_pack_write when MBOOT_ENABLE_PACKING=1.
// ---------------------------------------------------------------------------

static int test_9_pack_hook(int *failures) {
    fake_flash_reset_registry();
    fake_flash_init(&s_ff, PACK_TEST_BASE,
        (size_t)PACK_TEST_SECTOR_SIZE * PACK_TEST_SECTOR_COUNT,
        PACK_TEST_SECTOR_SIZE, s_flash_buf);
    fake_flash_register(&s_ff);
    TEST_REGIONS_SET(k_regions, 1);
    mboot_region_init();
    mboot_dfu_init();

    s_pack_write_calls = 0;

    uint8_t payload[4] = {0xDE, 0xAD, 0xBE, 0xEF};
    // mboot_dfu_on_dnload will call ensure_sector_erased then mboot_pack_write.
    // The stub returns -42; mboot_dfu_on_dnload maps that to DFU_STATUS_ERR_WRITE.
    fake_dfu_dnload(0, payload, 4);

    // The stub must have been called exactly once.
    if (s_pack_write_calls != 1) {
        printf("  FAIL %s:%d: mboot_pack_write called %d times (expected 1)\n",
            __FILE__, __LINE__, s_pack_write_calls);
        (*failures)++;
        return -1;
    }
    // Address must be the base of the region (block 0 * XFER_SIZE + base).
    if (s_pack_write_last_addr != PACK_TEST_BASE) {
        printf("  FAIL %s:%d: mboot_pack_write addr 0x%08lx (expected 0x%08lx)\n",
            __FILE__, __LINE__,
            (unsigned long)s_pack_write_last_addr,
            (unsigned long)PACK_TEST_BASE);
        (*failures)++;
        return -1;
    }
    return 0;
}

// ---------------------------------------------------------------------------
// main
// ---------------------------------------------------------------------------

int main(void) {
    int failures = 0;
    printf("=== test_pack_hook (MBOOT_ENABLE_PACKING=1) ===\n");
    RUN_TEST(test_9_pack_hook);
    if (failures == 0) {
        printf("  PASS (all tests)\n");
        printf("\nALL TESTS PASSED\n");
        return 0;
    } else {
        printf("  FAIL (%d failure(s))\n", failures);
        printf("\nTOTAL FAILURES: %d\n", failures);
        return 1;
    }
}
