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

// test_usbd.c — tests for mboot_usbd.c (descriptor builder and USB callbacks).
//
// Compiled with MBOOT_TESTS_FAKE_TUSB=1 so that mboot_usbd.c uses fake_tusb.h
// stubs instead of the real TinyUSB headers.

#include <stdint.h>
#include <string.h>

#include "mboot_api.h"
#include "mboot_dfu.h"
#include "mboot_region.h"
#include "mboot_usbd.h"
#include "fake_flash.h"
#include "fake_tusb.h"
#include "test_main.h"
#include "test_regions_config.h"

// ---------------------------------------------------------------------------
// Region layouts used by the descriptor-size tests
// ---------------------------------------------------------------------------

#define USBD_TEST_BASE (0x10000000u)
#define USBD_TEST_SECTOR_SIZE (4096u)
#define USBD_TEST_SECTOR_COUNT (8u)

static uint8_t s_flash_buf1[USBD_TEST_SECTOR_SIZE * USBD_TEST_SECTOR_COUNT];
static uint8_t s_flash_buf2[USBD_TEST_SECTOR_SIZE * USBD_TEST_SECTOR_COUNT];
static fake_flash_t s_ff1;
static fake_flash_t s_ff2;

static const mboot_region_t k_regions_1alt[] = {
    {
        .addr = USBD_TEST_BASE,
        .size = (mboot_addr_t)USBD_TEST_SECTOR_SIZE * USBD_TEST_SECTOR_COUNT,
        .sector_size = USBD_TEST_SECTOR_SIZE,
        .sector_count = USBD_TEST_SECTOR_COUNT,
        .name = "Flash",
        .flags = MBOOT_REGION_FLAG_READABLE | MBOOT_REGION_FLAG_ERASE_REQUIRED_BEFORE_WRITE,
    },
};

// Two regions with different names produce two alt settings.
static const mboot_region_t k_regions_2alt[] = {
    {
        .addr = USBD_TEST_BASE,
        .size = (mboot_addr_t)USBD_TEST_SECTOR_SIZE * USBD_TEST_SECTOR_COUNT,
        .sector_size = USBD_TEST_SECTOR_SIZE,
        .sector_count = USBD_TEST_SECTOR_COUNT,
        .name = "Flash A",
        .flags = MBOOT_REGION_FLAG_READABLE | MBOOT_REGION_FLAG_ERASE_REQUIRED_BEFORE_WRITE,
    },
    {
        .addr = USBD_TEST_BASE + (mboot_addr_t)USBD_TEST_SECTOR_SIZE * USBD_TEST_SECTOR_COUNT,
        .size = (mboot_addr_t)USBD_TEST_SECTOR_SIZE * USBD_TEST_SECTOR_COUNT,
        .sector_size = USBD_TEST_SECTOR_SIZE,
        .sector_count = USBD_TEST_SECTOR_COUNT,
        .name = "Flash B",
        .flags = MBOOT_REGION_FLAG_READABLE | MBOOT_REGION_FLAG_ERASE_REQUIRED_BEFORE_WRITE,
    },
};

// ---------------------------------------------------------------------------
// Setup helpers
// ---------------------------------------------------------------------------

static void setup_1alt(void) {
    fake_flash_reset_registry();
    fake_flash_init(&s_ff1, USBD_TEST_BASE,
        (size_t)USBD_TEST_SECTOR_SIZE * USBD_TEST_SECTOR_COUNT,
        USBD_TEST_SECTOR_SIZE, s_flash_buf1);
    fake_flash_register(&s_ff1);
    TEST_REGIONS_SET(k_regions_1alt, 1);
    mboot_region_init();
    mboot_dfu_init();
    fake_tusb_reset();
    mboot_usbd_init();
}

static void setup_2alt(void) {
    fake_flash_reset_registry();
    fake_flash_init(&s_ff1, USBD_TEST_BASE,
        (size_t)USBD_TEST_SECTOR_SIZE * USBD_TEST_SECTOR_COUNT,
        USBD_TEST_SECTOR_SIZE, s_flash_buf1);
    fake_flash_init(&s_ff2,
        USBD_TEST_BASE + (mboot_addr_t)USBD_TEST_SECTOR_SIZE * USBD_TEST_SECTOR_COUNT,
        (size_t)USBD_TEST_SECTOR_SIZE * USBD_TEST_SECTOR_COUNT,
        USBD_TEST_SECTOR_SIZE, s_flash_buf2);
    fake_flash_register(&s_ff1);
    fake_flash_register(&s_ff2);
    TEST_REGIONS_SET(k_regions_2alt, 2);
    mboot_region_init();
    mboot_dfu_init();
    fake_tusb_reset();
    mboot_usbd_init();
}

// ---------------------------------------------------------------------------
// Test 40: After mboot_usbd_init no leave is pending.
// ---------------------------------------------------------------------------

static int test_40_init(int *failures) {
    setup_1alt();
    TEST_ASSERT(!mboot_usbd_leave_requested());
    return 0;
}

// ---------------------------------------------------------------------------
// Test 41: tud_dfu_download_cb with a valid alt commits the active region.
//
// The lazy sync_alt path in mboot_usbd.c detects the SET_INTERFACE-implied
// alt switch when the first DNLOAD callback arrives carrying the new value.
// ---------------------------------------------------------------------------

static int test_41_set_alt_valid(int *failures) {
    setup_2alt();
    static uint8_t data[4] = {0xAA, 0xBB, 0xCC, 0xDD};
    fake_tusb_reset();

    // First DNLOAD into alt 1: must update the active region.
    tud_dfu_download_cb(1, 0, data, 4);
    TEST_ASSERT(fake_tusb_last_finish_called);
    TEST_ASSERT_EQ(fake_tusb_last_finish_status, DFU_STATUS_OK);
    TEST_ASSERT_EQ(mboot_region_get_active(), 1);

    // Switch back to alt 0 on the next DNLOAD.
    fake_tusb_reset();
    tud_dfu_download_cb(0, 0, data, 4);
    TEST_ASSERT(fake_tusb_last_finish_called);
    TEST_ASSERT_EQ(fake_tusb_last_finish_status, DFU_STATUS_OK);
    TEST_ASSERT_EQ(mboot_region_get_active(), 0);
    return 0;
}

// ---------------------------------------------------------------------------
// Test 42: tud_dfu_download_cb with an out-of-range alt fails with ERR_TARGET
// and does not commit the alt or write flash.
// ---------------------------------------------------------------------------

static int test_42_set_alt_invalid(int *failures) {
    setup_1alt();
    fake_flash_reset_counters(&s_ff1);
    fake_tusb_reset();

    static uint8_t data[4] = {0};
    // Only alt 0 is valid; a DNLOAD for alt 1 must be rejected with
    // ERR_TARGET, no flash side effect, active alt unchanged.
    tud_dfu_download_cb(1, 0, data, 4);
    TEST_ASSERT(fake_tusb_last_finish_called);
    TEST_ASSERT_EQ(fake_tusb_last_finish_status, DFU_STATUS_ERR_TARGET);
    TEST_ASSERT_EQ(s_ff1.erase_calls, 0u);
    TEST_ASSERT_EQ(s_ff1.write_calls, 0u);
    TEST_ASSERT_EQ(mboot_region_get_active(), 0);
    return 0;
}

// ---------------------------------------------------------------------------
// Test 43: Switching alt mid-session clears the touched-sector bitmap.
//
// Write to alt 0 sector 0 (one erase), then a DNLOAD into alt 1 sector 0
// must trigger a fresh erase rather than skipping one whose bit was set in
// alt 0's bitmap.
// ---------------------------------------------------------------------------

static int test_43_set_alt_notify(int *failures) {
    setup_2alt();
    fake_flash_reset_counters(&s_ff1);
    fake_flash_reset_counters(&s_ff2);
    fake_tusb_reset();

    static uint8_t data[4] = {1, 2, 3, 4};
    tud_dfu_download_cb(0, 0, data, 4);
    TEST_ASSERT_EQ(s_ff1.erase_calls, 1u);
    TEST_ASSERT_EQ(s_ff2.erase_calls, 0u);

    // Switch to alt 1 mid-session.  Bitmap must be cleared so alt 1's
    // sector 0 is freshly erased.
    fake_tusb_reset();
    tud_dfu_download_cb(1, 0, data, 4);
    TEST_ASSERT_EQ(s_ff2.erase_calls, 1u);
    TEST_ASSERT_EQ(mboot_region_get_active(), 1);
    return 0;
}

// ---------------------------------------------------------------------------
// Test 44: tud_dfu_download_cb triggers erase-before-write via mboot_dfu_on_dnload.
//
// Phase A — first download into a virgin sector: 1 erase + 1 write.
// Phase B — second download into the same sector: 0 additional erases + 1 write.
//           The touched-sector bitmap prevents a redundant erase on re-flash.
// ---------------------------------------------------------------------------

static int test_44_download_cb(int *failures) {
    setup_1alt();
    fake_flash_reset_counters(&s_ff1);
    fake_tusb_reset();

    static uint8_t data[64];
    memset(data, 0x5A, sizeof(data));

    // Phase A: first download to block 0 (virgin sector).
    tud_dfu_download_cb(0, 0, data, 64);

    TEST_ASSERT(fake_tusb_last_finish_called);
    TEST_ASSERT_EQ(fake_tusb_last_finish_status, DFU_STATUS_OK);
    TEST_ASSERT_EQ(s_ff1.erase_calls, 1u);
    TEST_ASSERT_EQ(s_ff1.write_calls, 1u);

    // Phase B: second download to the same block within the same session.
    // The touched-sector bitmap has sector 0 set; no additional erase is issued.
    fake_tusb_reset();
    fake_flash_reset_counters(&s_ff1);

    tud_dfu_download_cb(0, 0, data, 64);

    TEST_ASSERT(fake_tusb_last_finish_called);
    TEST_ASSERT_EQ(fake_tusb_last_finish_status, DFU_STATUS_OK);
    TEST_ASSERT_EQ(s_ff1.erase_calls, 0u);
    TEST_ASSERT_EQ(s_ff1.write_calls, 1u);
    return 0;
}

// ---------------------------------------------------------------------------
// Test 45: tud_dfu_manifest_cb calls finish_flashing with DFU_STATUS_OK.
// ---------------------------------------------------------------------------

static int test_45_manifest_cb(int *failures) {
    setup_1alt();
    fake_tusb_reset();
    tud_dfu_manifest_cb(0);
    TEST_ASSERT(fake_tusb_last_finish_called);
    TEST_ASSERT_EQ(fake_tusb_last_finish_status, DFU_STATUS_OK);
    return 0;
}

// ---------------------------------------------------------------------------
// Test 46: vendor opcode 0x80 at SETUP stage requests the 8-byte DATA stage.
// ---------------------------------------------------------------------------

static int test_46_vendor_setup(int *failures) {
    setup_1alt();
    fake_tusb_reset();

    tusb_control_request_t req;
    memset(&req, 0, sizeof(req));
    req.bmRequestType = 0x41;
    req.bRequest = 0x80; // MBOOT_VREQ_ERASE
    req.wLength = 8;

    bool ok = tud_vendor_control_xfer_cb(0, CONTROL_STAGE_SETUP, &req);
    TEST_ASSERT(ok);
    TEST_ASSERT(fake_tusb_last_xfer_called);
    TEST_ASSERT_EQ(fake_tusb_last_xfer_len, 8);
    return 0;
}

// ---------------------------------------------------------------------------
// Test 47: unknown vendor opcode at SETUP returns false (STALL).
// ---------------------------------------------------------------------------

static int test_47_vendor_unknown_opcode(int *failures) {
    setup_1alt();

    tusb_control_request_t req;
    memset(&req, 0, sizeof(req));
    req.bmRequestType = 0x41;
    req.bRequest = 0x81; // Reserved; not 0x80
    req.wLength = 8;

    bool ok = tud_vendor_control_xfer_cb(0, CONTROL_STAGE_SETUP, &req);
    TEST_ASSERT(!ok);
    return 0;
}

// ---------------------------------------------------------------------------
// Test 48: vendor SETUP with wrong wLength returns false (STALL).
// ---------------------------------------------------------------------------

static int test_48_vendor_wrong_length(int *failures) {
    setup_1alt();

    tusb_control_request_t req;
    memset(&req, 0, sizeof(req));
    req.bmRequestType = 0x41;
    req.bRequest = 0x80;
    req.wLength = 4; // wrong: should be 8

    bool ok = tud_vendor_control_xfer_cb(0, CONTROL_STAGE_SETUP, &req);
    TEST_ASSERT(!ok);
    return 0;
}

// ---------------------------------------------------------------------------
// Test 49: configuration descriptor for 1-alt setup has the expected length.
//
// Expected: 9 (config) + 1 * 9 (interface) + 9 (DFU functional) = 27 bytes.
// ---------------------------------------------------------------------------

static int test_49_cfg_desc_1alt_len(int *failures) {
    setup_1alt();
    const uint8_t *desc = tud_descriptor_configuration_cb(0);
    TEST_ASSERT_NOTNULL(desc);
    // Bytes 2-3 of the configuration descriptor are wTotalLength (LE).
    uint16_t total_len = (uint16_t)(desc[2] | ((uint16_t)desc[3] << 8));
    TEST_ASSERT_EQ(total_len, 27u);
    return 0;
}

// ---------------------------------------------------------------------------
// Test 50: configuration descriptor for 2-alt setup has the expected length.
//
// Expected: 9 (config) + 2 * 9 (interfaces) + 9 (DFU functional) = 36 bytes.
// ---------------------------------------------------------------------------

static int test_50_cfg_desc_2alt_len(int *failures) {
    setup_2alt();
    const uint8_t *desc = tud_descriptor_configuration_cb(0);
    TEST_ASSERT_NOTNULL(desc);
    uint16_t total_len = (uint16_t)(desc[2] | ((uint16_t)desc[3] << 8));
    TEST_ASSERT_EQ(total_len, 36u);
    return 0;
}

// ---------------------------------------------------------------------------
// Test 51: string descriptor index 0 returns the LANGID descriptor.
// ---------------------------------------------------------------------------

static int test_51_str_langid(int *failures) {
    setup_1alt();
    const uint16_t *desc = tud_descriptor_string_cb(0, 0);
    TEST_ASSERT_NOTNULL(desc);
    const uint8_t *b = (const uint8_t *)desc;
    TEST_ASSERT_EQ(b[0], 4u);     // bLength
    TEST_ASSERT_EQ(b[1], 0x03u);  // bDescriptorType: string
    TEST_ASSERT_EQ(b[2], 0x09u);  // English US low byte
    TEST_ASSERT_EQ(b[3], 0x04u);  // English US high byte
    return 0;
}

// ---------------------------------------------------------------------------
// Test 52: string descriptor for alt 0 (index 4) is non-NULL and starts with
// the USB string descriptor header (length >= 2, type = 0x03).
// ---------------------------------------------------------------------------

static int test_52_str_alt(int *failures) {
    setup_1alt();
    const uint16_t *desc = tud_descriptor_string_cb(4, 0);
    TEST_ASSERT_NOTNULL(desc);
    const uint8_t *b = (const uint8_t *)desc;
    TEST_ASSERT_EQ(b[1], 0x03u); // USB string descriptor type
    TEST_ASSERT(b[0] >= 2u);     // bLength at least 2
    return 0;
}

// ---------------------------------------------------------------------------
// Test 53: string descriptor for an out-of-range alt returns NULL.
// ---------------------------------------------------------------------------

static int test_53_str_alt_out_of_range(int *failures) {
    setup_1alt();
    // Index 5 = alt 1, which does not exist in a 1-alt setup.
    const uint16_t *desc = tud_descriptor_string_cb(5, 0);
    TEST_ASSERT_NULL(desc);
    return 0;
}

// ---------------------------------------------------------------------------
// Test entry point
// ---------------------------------------------------------------------------

int test_usbd(void) {
    int failures = 0;

    RUN_TEST(test_40_init);
    RUN_TEST(test_41_set_alt_valid);
    RUN_TEST(test_42_set_alt_invalid);
    RUN_TEST(test_43_set_alt_notify);
    RUN_TEST(test_44_download_cb);
    RUN_TEST(test_45_manifest_cb);
    RUN_TEST(test_46_vendor_setup);
    RUN_TEST(test_47_vendor_unknown_opcode);
    RUN_TEST(test_48_vendor_wrong_length);
    RUN_TEST(test_49_cfg_desc_1alt_len);
    RUN_TEST(test_50_cfg_desc_2alt_len);
    RUN_TEST(test_51_str_langid);
    RUN_TEST(test_52_str_alt);
    RUN_TEST(test_53_str_alt_out_of_range);

    return failures;
}
