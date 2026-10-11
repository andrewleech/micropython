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

// test_hooks.c - hooks (session begin, manifest, result), read-only regions,
// region ordering and the DFU status mapping in mboot_usbd.c.
//
// The mboot_hook_* functions below record their calls and return configurable
// values. The defaults (session begin and manifest accept, result stalls) leave
// the other test modules in this binary unaffected.

#include <errno.h>
#include <stdint.h>
#include <string.h>

#include "fake_flash.h"
#include "fake_transport.h"
#include "fake_tusb.h"
#include "mboot_api.h"
#include "mboot_dfu.h"
#include "mboot_region.h"
#include "mboot_usbd.h"
#include "test_main.h"
#include "test_regions_config.h"

#ifndef DFU_STATUS_ERR_FILE
#define DFU_STATUS_ERR_FILE (0x02u)
#endif


// ---------------------------------------------------------------------------
// Hooks
// ---------------------------------------------------------------------------

#define TH_MAX 16

static int th_begin_rc;
static unsigned th_begin_calls;
static uint8_t th_begin_alt[TH_MAX];
static unsigned th_begin_erases[TH_MAX];   // flash erase count when the hook ran
static unsigned th_begin_writes[TH_MAX];   // flash write count when the hook ran

static uint8_t th_manifest_status;
static unsigned th_manifest_calls;
static uint8_t th_manifest_alt;

static uint16_t th_result_ret;
static uint8_t th_result_bytes[MBOOT_VREQ_RESULT_LEN];

static fake_flash_t *th_ff_a;
static fake_flash_t *th_ff_b;

int mboot_hook_session_begin(uint8_t alt) {
    if (th_begin_calls < TH_MAX) {
        th_begin_alt[th_begin_calls] = alt;
        th_begin_erases[th_begin_calls] = fake_flash_total_erases();
        th_begin_writes[th_begin_calls] = fake_flash_total_writes();
    }
    th_begin_calls++;
    return th_begin_rc;
}

uint8_t mboot_hook_manifest(uint8_t alt) {
    th_manifest_calls++;
    th_manifest_alt = alt;
    return th_manifest_status;
}

uint16_t mboot_hook_get_result(uint8_t *buf, uint16_t len) {
    if (th_result_ret == MBOOT_VREQ_RESULT_LEN && len >= MBOOT_VREQ_RESULT_LEN) {
        memcpy(buf, th_result_bytes, MBOOT_VREQ_RESULT_LEN);
    }
    return th_result_ret;
}

// ---------------------------------------------------------------------------
// Fixture: alt 0 "Flash A", alt 1 "Flash B" (both writable) and alt 2 "Log"
// (read-only), with the log below the others in the address space.
// ---------------------------------------------------------------------------

#define A_BASE (0x30000000u)
#define B_BASE (0x30010000u)
#define L_BASE (0x2FFF0000u)
#define SECT (4096u)
#define A_SECTORS (8u)
#define B_SECTORS (4u)
#define L_SECTORS (2u)

static uint8_t s_buf_a[SECT * A_SECTORS];
static uint8_t s_buf_b[SECT * B_SECTORS];
static uint8_t s_buf_l[SECT * L_SECTORS];
static fake_flash_t s_ff_a, s_ff_b, s_ff_l;

#define RW (MBOOT_REGION_FLAG_READABLE | MBOOT_REGION_FLAG_ERASE_REQUIRED_BEFORE_WRITE)
static const mboot_region_t k_regions[] = {
    { .addr = A_BASE, .size = SECT * A_SECTORS, .sector_size = SECT, .sector_count = A_SECTORS,
      .name = "Flash A", .flags = RW },
    { .addr = B_BASE, .size = SECT * B_SECTORS, .sector_size = SECT, .sector_count = B_SECTORS,
      .name = "Flash B", .flags = RW },
    { .addr = L_BASE, .size = SECT * L_SECTORS, .sector_size = SECT, .sector_count = L_SECTORS,
      .name = "Log", .flags = MBOOT_REGION_FLAG_READABLE | MBOOT_REGION_FLAG_READ_ONLY },
};

static int setup(void) {
    fake_flash_reset_registry();
    fake_flash_init(&s_ff_a, A_BASE, sizeof(s_buf_a), SECT, s_buf_a);
    fake_flash_init(&s_ff_b, B_BASE, sizeof(s_buf_b), SECT, s_buf_b);
    fake_flash_init(&s_ff_l, L_BASE, sizeof(s_buf_l), SECT, s_buf_l);
    // The log is read-only, so init must not ask the port about it.
    s_ff_l.writable = 0;
    fake_flash_register(&s_ff_a);
    fake_flash_register(&s_ff_b);
    fake_flash_register(&s_ff_l);
    th_ff_a = &s_ff_a;
    th_ff_b = &s_ff_b;
    th_begin_rc = 0;
    th_begin_calls = 0;
    th_manifest_status = 0;
    th_manifest_calls = 0;
    th_result_ret = 0;
    TEST_REGIONS_SET(k_regions, 3);
    int rc = mboot_region_init();
    mboot_dfu_init();
    mboot_region_set_active(0);
    fake_tusb_reset();
    mboot_usbd_init();
    return rc;
}

static uint8_t s_data[2048];

static uint8_t dnload_cb(uint8_t alt, uint16_t block, uint16_t len) {
    fake_tusb_reset();
    tud_dfu_download_cb(alt, block, s_data, len);
    return fake_tusb_last_finish_status;
}

static bool vendor_erase_cb(uint16_t wValue, uint32_t addr, uint32_t length) {
    tusb_control_request_t req = { .bmRequestType = 0x41, .bRequest = 0x80, .wValue = wValue,
                                   .wIndex = 0, .wLength = 8 };
    fake_tusb_reset();
    if (!tud_vendor_control_xfer_cb(0, CONTROL_STAGE_SETUP, &req)) {
        return false;
    }
    uint8_t *p = fake_tusb_last_xfer_buf;
    for (int i = 0; i < 4; i++) {
        p[i] = (uint8_t)(addr >> (8 * i));
        p[4 + i] = (uint8_t)(length >> (8 * i));
    }
    // The erase runs in the DATA stage (a false return stalls the status stage).
    // The ACK stage after it does nothing.
    bool ok = tud_vendor_control_xfer_cb(0, CONTROL_STAGE_DATA, &req);
    (void)tud_vendor_control_xfer_cb(0, CONTROL_STAGE_ACK, &req);
    return ok;
}

// ---------------------------------------------------------------------------
// Test 60: Regions of different alts can be in any address order.
// ---------------------------------------------------------------------------

static int test_60_alt_order_and_read_only_init(int *failures) {
    TEST_ASSERT_EQ(setup(), 0);
    TEST_ASSERT_EQ(mboot_region_count(), 3);
    TEST_ASSERT_EQ(mboot_region_alt_is_read_only(0), 0);
    TEST_ASSERT_EQ(mboot_region_alt_is_read_only(1), 0);
    TEST_ASSERT_EQ(mboot_region_alt_is_read_only(2), 1);
    TEST_ASSERT_EQ(mboot_region_alt_is_read_only(3), 0);
    return 0;
}

// ---------------------------------------------------------------------------
// Test 61: Overlapping regions and a gap between same-name regions are rejected.
// ---------------------------------------------------------------------------

static int test_61_overlap_rejected(int *failures) {
    setup();
    mboot_region_t over[2] = { k_regions[0], k_regions[1] };
    over[1].addr = A_BASE + SECT;   // inside region 0
    TEST_REGIONS_SET(over, 2);
    TEST_ASSERT(mboot_region_init() < 0);

    mboot_region_t gap[2] = { k_regions[0], k_regions[0] };
    gap[1].addr = A_BASE + SECT * A_SECTORS + SECT;   // same name, one sector gap
    TEST_REGIONS_SET(gap, 2);
    TEST_ASSERT(mboot_region_init() < 0);

    // A different name may sit anywhere that does not overlap.
    mboot_region_t apart[2] = { k_regions[0], k_regions[1] };
    TEST_REGIONS_SET(apart, 2);
    TEST_ASSERT_EQ(mboot_region_init(), 0);
    return 0;
}

// ---------------------------------------------------------------------------
// Test 62: Read-only region: write and erase fail with -EACCES, read works and
// the alt string ends in 'a'.
// ---------------------------------------------------------------------------

static int test_62_read_only_region(int *failures) {
    setup();
    uint8_t buf[16] = { 0 };
    memset(s_buf_l, 0x5A, sizeof(s_buf_l));
    mboot_region_set_active(2);
    TEST_ASSERT_EQ(mboot_region_write(L_BASE, buf, 16), -EACCES);
    mboot_addr_t next;
    TEST_ASSERT_EQ(mboot_region_erase_page(L_BASE, &next), -EACCES);
    TEST_ASSERT_EQ(mboot_region_erase_page(L_BASE + SECT, &next), -EACCES);
    TEST_ASSERT_EQ(s_ff_l.write_calls, 0u);
    TEST_ASSERT_EQ(s_ff_l.erase_calls, 0u);
    TEST_ASSERT_EQ(s_buf_l[0], 0x5A);
    TEST_ASSERT_EQ(mboot_region_read(L_BASE, buf, 16), 0);
    TEST_ASSERT_EQ(buf[0], 0x5A);

    // A range outside the active alt reports -ERANGE first.
    TEST_ASSERT_EQ(mboot_region_write(A_BASE, buf, 16), -ERANGE);

    uint16_t u16[1 + MBOOT_REGION_DESC_MAX_CONTENT_CHARS];
    int n = mboot_region_get_alt_string(2, u16, MBOOT_REGION_DESC_MAX_CONTENT_CHARS);
    TEST_ASSERT(n > 0);
    TEST_ASSERT_EQ(u16[n], 'a');   // last character: permission
    n = mboot_region_get_alt_string(0, u16, MBOOT_REGION_DESC_MAX_CONTENT_CHARS);
    TEST_ASSERT_EQ(u16[n], 'g');
    return 0;
}

// A read-only region does not make a mixed alt read-only, but a write that
// overlaps the read-only part is refused.
static int test_63_read_only_part_of_alt(int *failures) {
    setup();
    mboot_region_t mix[2] = { k_regions[0], k_regions[0] };
    mix[1].addr = A_BASE + SECT * A_SECTORS;
    mix[1].size = SECT;
    mix[1].sector_count = 1;
    mix[1].flags = MBOOT_REGION_FLAG_READABLE | MBOOT_REGION_FLAG_READ_ONLY;
    fake_flash_reset_registry();
    fake_flash_init(&s_ff_a, A_BASE, sizeof(s_buf_a), SECT, s_buf_a);
    fake_flash_register(&s_ff_a);
    static uint8_t extra[SECT];
    static fake_flash_t ff_extra;
    fake_flash_init(&ff_extra, A_BASE + SECT * A_SECTORS, SECT, SECT, extra);
    fake_flash_register(&ff_extra);
    TEST_REGIONS_SET(mix, 2);
    TEST_ASSERT_EQ(mboot_region_init(), 0);
    TEST_ASSERT_EQ(mboot_region_count(), 1);
    TEST_ASSERT_EQ(mboot_region_alt_is_read_only(0), 0);
    uint8_t buf[2 * 16] = { 0 };
    // Entirely in the writable part.
    TEST_ASSERT_EQ(mboot_region_write(A_BASE, buf, 16), 0);
    // Spanning into the read-only part.
    TEST_ASSERT_EQ(mboot_region_write(A_BASE + SECT * A_SECTORS - 16, buf, 32), -EACCES);
    mboot_addr_t next;
    TEST_ASSERT_EQ(mboot_region_erase_page(A_BASE + SECT * A_SECTORS, &next), -EACCES);
    return 0;
}

// ---------------------------------------------------------------------------
// Test 64: The session-begin hook runs before the first erase or write, once per session.
// ---------------------------------------------------------------------------

static int test_64_session_begin_order(int *failures) {
    setup();
    memset(s_data, 0x42, sizeof(s_data));
    TEST_ASSERT_EQ(dnload_cb(0, 0, 64), DFU_STATUS_OK);
    TEST_ASSERT_EQ(th_begin_calls, 1u);
    TEST_ASSERT_EQ(th_begin_alt[0], 0);
    TEST_ASSERT_EQ(th_begin_erases[0], 0u);   // nothing erased before the hook
    TEST_ASSERT_EQ(th_begin_writes[0], 0u);
    TEST_ASSERT_EQ(s_ff_a.erase_calls, 1u);
    TEST_ASSERT_EQ(s_ff_a.write_calls, 1u);

    // Further blocks of the session do not call it again.
    TEST_ASSERT_EQ(dnload_cb(0, 1, 64), DFU_STATUS_OK);
    TEST_ASSERT_EQ(dnload_cb(0, 2, 64), DFU_STATUS_OK);
    TEST_ASSERT_EQ(th_begin_calls, 1u);

    // A different alt starts a new session; so does an abort of the same alt.
    TEST_ASSERT_EQ(dnload_cb(1, 0, 64), DFU_STATUS_OK);
    TEST_ASSERT_EQ(th_begin_calls, 2u);
    TEST_ASSERT_EQ(th_begin_alt[1], 1);
    tud_dfu_abort_cb(1);
    TEST_ASSERT(!mboot_dfu_session_active());
    TEST_ASSERT_EQ(dnload_cb(1, 0, 64), DFU_STATUS_OK);
    TEST_ASSERT_EQ(th_begin_calls, 3u);
    TEST_ASSERT(mboot_dfu_session_active());
    return 0;
}

// A failing hook stops the request before any flash access and maps to
// errERASE. The next request tries again.
static int test_65_session_begin_failure(int *failures) {
    setup();
    memset(s_data, 0x42, sizeof(s_data));
    th_begin_rc = -5;
    TEST_ASSERT_EQ(fake_dfu_dnload(0, s_data, 64), MBOOT_DFU_ERR_SESSION_BEGIN);
    TEST_ASSERT_EQ(dnload_cb(0, 0, 64), DFU_STATUS_ERR_ERASE);
    TEST_ASSERT_EQ(s_ff_a.erase_calls, 0u);
    TEST_ASSERT_EQ(s_ff_a.write_calls, 0u);
    TEST_ASSERT_EQ(th_begin_calls, 2u);
    TEST_ASSERT(!mboot_dfu_session_active());

    // Vendor erase: stalled, nothing erased.
    TEST_ASSERT(!vendor_erase_cb(0, 0, 0xFFFFFFFFu));
    TEST_ASSERT_EQ(s_ff_a.erase_calls, 0u);

    th_begin_rc = 0;
    TEST_ASSERT_EQ(dnload_cb(0, 0, 64), DFU_STATUS_OK);
    TEST_ASSERT_EQ(s_ff_a.erase_calls, 1u);
    return 0;
}

// ---------------------------------------------------------------------------
// Test 66: Vendor erase runs the hook before the first erase, refused requests
// have no side effects, and erasing the read-only alt fails.
// ---------------------------------------------------------------------------

static int test_66_vendor_erase_hook(int *failures) {
    setup();
    TEST_ASSERT(vendor_erase_cb(0, 0, 0xFFFFFFFFu));
    TEST_ASSERT_EQ(th_begin_calls, 1u);
    TEST_ASSERT_EQ(th_begin_erases[0], 0u);
    TEST_ASSERT_EQ(s_ff_a.erase_calls, A_SECTORS);
    TEST_ASSERT_EQ(s_ff_b.erase_calls, 0u);
    TEST_ASSERT_EQ(s_ff_l.erase_calls, 0u);

    // Mass erase of the read-only alt (selected by wValue): refused, no hook.
    TEST_ASSERT(!vendor_erase_cb(2, 0, 0xFFFFFFFFu));
    TEST_ASSERT(!vendor_erase_cb(2, L_BASE, SECT));
    TEST_ASSERT_EQ(th_begin_calls, 1u);
    TEST_ASSERT_EQ(s_ff_l.erase_calls, 0u);

    setup();
    // A range starting outside the alt, a range that wraps and a zero-length
    // range: no hook, no erase.
    TEST_ASSERT(!vendor_erase_cb(0, A_BASE - SECT, 2 * SECT));
    TEST_ASSERT(!vendor_erase_cb(0, A_BASE + SECT * A_SECTORS, SECT));
    TEST_ASSERT(!vendor_erase_cb(0, A_BASE, 0xFFFFFFF0u));
    TEST_ASSERT(vendor_erase_cb(0, A_BASE, 0));
    TEST_ASSERT_EQ(th_begin_calls, 0u);
    TEST_ASSERT_EQ(fake_flash_total_erases(), 0u);

    // A range that starts inside and runs past the end erases the inside part, then fails.
    TEST_ASSERT(!vendor_erase_cb(0, A_BASE + SECT * (A_SECTORS - 1), 3 * SECT));
    TEST_ASSERT_EQ(th_begin_calls, 1u);
    TEST_ASSERT_EQ(s_ff_a.erase_calls, 1u);
    TEST_ASSERT_EQ(s_ff_b.erase_calls, 0u);
    return 0;
}

// Erasing an alt other than the session's must not leave touched bits that make
// the session skip a needed erase.
static int test_67_cross_alt_erase_resets_state(int *failures) {
    setup();
    memset(s_data, 0x42, sizeof(s_data));
    TEST_ASSERT_EQ(dnload_cb(0, 0, 64), DFU_STATUS_OK);
    TEST_ASSERT_EQ(s_ff_a.erase_calls, 1u);
    // Erase alt 1 sector 0 while alt 0 is the session alt.
    TEST_ASSERT(vendor_erase_cb(1, B_BASE, SECT));
    TEST_ASSERT_EQ(s_ff_b.erase_calls, 1u);
    // Alt 0 sector 0 is erased again before the next write.
    TEST_ASSERT_EQ(dnload_cb(0, 0, 64), DFU_STATUS_OK);
    TEST_ASSERT_EQ(s_ff_a.erase_calls, 2u);
    return 0;
}

// ---------------------------------------------------------------------------
// Test 68: DFU status for failed requests.
// ---------------------------------------------------------------------------

static int test_68_dnload_status(int *failures) {
    setup();
    memset(s_data, 0x42, sizeof(s_data));
    // Past the end of the alt, and the maximum block number: errADDRESS, no flash access.
    TEST_ASSERT_EQ(dnload_cb(0, (uint16_t)(A_SECTORS * SECT / MBOOT_DFU_XFER_SIZE), 64), DFU_STATUS_ERR_ADDRESS);
    TEST_ASSERT_EQ(dnload_cb(0, 65535, 64), DFU_STATUS_ERR_ADDRESS);
    TEST_ASSERT_EQ(th_begin_calls, 0u);
    // Read-only alt: errADDRESS, no hook.
    TEST_ASSERT_EQ(dnload_cb(2, 0, 64), DFU_STATUS_ERR_ADDRESS);
    TEST_ASSERT_EQ(th_begin_calls, 0u);
    TEST_ASSERT_EQ(fake_flash_total_erases(), 0u);
    TEST_ASSERT_EQ(fake_flash_total_writes(), 0u);
    // The last block inside the alt works.
    TEST_ASSERT_EQ(dnload_cb(0, (uint16_t)(A_SECTORS * SECT / MBOOT_DFU_XFER_SIZE - 1), 64), DFU_STATUS_OK);
    return 0;
}

// ---------------------------------------------------------------------------
// Test 69: The manifest hook status reaches TinyUSB, and a failure does not
// request the leave. Has to run before test_45 in test_usbd, which latches the
// leave request with a successful manifest.
// ---------------------------------------------------------------------------

static int test_69_manifest_hook(int *failures) {
    setup();
    th_manifest_status = DFU_STATUS_ERR_FILE;
    fake_tusb_reset();
    tud_dfu_manifest_cb(1);
    TEST_ASSERT_EQ(th_manifest_calls, 1u);
    TEST_ASSERT_EQ(th_manifest_alt, 1);
    TEST_ASSERT(fake_tusb_last_finish_called);
    TEST_ASSERT_EQ(fake_tusb_last_finish_status, DFU_STATUS_ERR_FILE);
    mboot_usbd_bus_reset();
    TEST_ASSERT(!mboot_usbd_leave_requested());

    // An alt past the table does not reach the hook.
    fake_tusb_reset();
    tud_dfu_manifest_cb(9);
    TEST_ASSERT_EQ(th_manifest_calls, 1u);
    TEST_ASSERT_EQ(fake_tusb_last_finish_status, DFU_STATUS_ERR_TARGET);
    return 0;
}

// ---------------------------------------------------------------------------
// Test 70: Vendor request 0x81.
// ---------------------------------------------------------------------------

static bool result_request(uint8_t bmRequestType, uint16_t wLength, uint16_t wIndex) {
    tusb_control_request_t req = { .bmRequestType = bmRequestType, .bRequest = 0x81,
                                   .wValue = 0, .wIndex = wIndex, .wLength = wLength };
    fake_tusb_reset();
    return tud_vendor_control_xfer_cb(0, CONTROL_STAGE_SETUP, &req);
}

static int test_70_result_request(int *failures) {
    setup();
    for (int i = 0; i < 16; i++) {
        th_result_bytes[i] = (uint8_t)(0xA0 + i);
    }
    // Default (hook returns 0): stalled.
    TEST_ASSERT(!result_request(0xC1, 16, 0));

    th_result_ret = MBOOT_VREQ_RESULT_LEN;
    TEST_ASSERT(result_request(0xC1, 16, 0));
    TEST_ASSERT(fake_tusb_last_xfer_called);
    TEST_ASSERT_EQ(fake_tusb_last_xfer_len, 16);
    TEST_ASSERT(memcmp(fake_tusb_last_xfer_buf, th_result_bytes, 16) == 0);

    // DATA and ACK stages complete without another transfer.
    tusb_control_request_t req = { .bmRequestType = 0xC1, .bRequest = 0x81, .wLength = 16 };
    fake_tusb_reset();
    TEST_ASSERT(tud_vendor_control_xfer_cb(0, CONTROL_STAGE_DATA, &req));
    TEST_ASSERT(tud_vendor_control_xfer_cb(0, CONTROL_STAGE_ACK, &req));
    TEST_ASSERT(!fake_tusb_last_xfer_called);

    // Wrong length, wrong direction, wrong interface, short hook reply.
    TEST_ASSERT(!result_request(0xC1, 8, 0));
    TEST_ASSERT(!result_request(0xC1, 17, 0));
    TEST_ASSERT(!result_request(0x41, 16, 0));
    TEST_ASSERT(!result_request(0xC1, 16, 1));
    th_result_ret = 8;
    TEST_ASSERT(!result_request(0xC1, 16, 0));
    th_result_ret = 0;
    return 0;
}

// ---------------------------------------------------------------------------
// Test 71: The download session ends on a failed block, a rejected manifest and
// a bus reset. The next block of the same alt runs the session begin hook again
// and erases its sector again. Otherwise a retry from block 0 would write onto
// sectors the first transfer had programmed.
// ---------------------------------------------------------------------------

static int test_71_session_ends(int *failures) {
    setup();
    memset(s_data, 0x42, sizeof(s_data));
    const uint16_t past_end = (uint16_t)(A_SECTORS * SECT / MBOOT_DFU_XFER_SIZE);

    TEST_ASSERT_EQ(dnload_cb(0, 0, 64), DFU_STATUS_OK);
    TEST_ASSERT(mboot_dfu_session_active());
    TEST_ASSERT_EQ(dnload_cb(0, past_end, 64), DFU_STATUS_ERR_ADDRESS);
    TEST_ASSERT(!mboot_dfu_session_active());
    TEST_ASSERT_EQ(dnload_cb(0, 0, 64), DFU_STATUS_OK);
    TEST_ASSERT_EQ(th_begin_calls, 2u);
    TEST_ASSERT_EQ(s_ff_a.erase_calls, 2u);

    th_manifest_status = DFU_STATUS_ERR_FILE;
    fake_tusb_reset();
    tud_dfu_manifest_cb(0);
    TEST_ASSERT_EQ(fake_tusb_last_finish_status, DFU_STATUS_ERR_FILE);
    TEST_ASSERT(!mboot_dfu_session_active());
    TEST_ASSERT_EQ(dnload_cb(0, 0, 64), DFU_STATUS_OK);
    TEST_ASSERT_EQ(th_begin_calls, 3u);
    TEST_ASSERT_EQ(s_ff_a.erase_calls, 3u);

    mboot_usbd_bus_reset();
    TEST_ASSERT(!mboot_dfu_session_active());
    TEST_ASSERT(!mboot_usbd_leave_requested());
    TEST_ASSERT_EQ(dnload_cb(0, 0, 64), DFU_STATUS_OK);
    TEST_ASSERT_EQ(th_begin_calls, 4u);
    TEST_ASSERT_EQ(s_ff_a.erase_calls, 4u);
    return 0;
}

// ---------------------------------------------------------------------------
// Test 72: A bus reset is detected by polling mboot_usbd_leave_requested(),
// because TinyUSB drops the configuration on reset and has no callback for the
// application. A reset ends the session, and requests the leave only after a
// successful manifest.
// ---------------------------------------------------------------------------

static int test_72_bus_reset_is_polled(int *failures) {
    setup();
    memset(s_data, 0x42, sizeof(s_data));

    fake_tusb_mounted = false;
    TEST_ASSERT(!mboot_usbd_leave_requested());
    fake_tusb_mounted = true;
    TEST_ASSERT(!mboot_usbd_leave_requested());
    TEST_ASSERT_EQ(dnload_cb(0, 0, 64), DFU_STATUS_OK);
    TEST_ASSERT(mboot_dfu_session_active());
    TEST_ASSERT(!mboot_usbd_leave_requested());
    TEST_ASSERT(mboot_dfu_session_active());

    // Reset in the middle of a download: the session ends, no leave.
    fake_tusb_mounted = false;
    TEST_ASSERT(!mboot_usbd_leave_requested());
    TEST_ASSERT(!mboot_dfu_session_active());

    // Enumerated again, a download and a rejected manifest, then another reset: no leave.
    fake_tusb_mounted = true;
    TEST_ASSERT(!mboot_usbd_leave_requested());
    TEST_ASSERT_EQ(dnload_cb(0, 0, 64), DFU_STATUS_OK);
    th_manifest_status = DFU_STATUS_ERR_FILE;
    tud_dfu_manifest_cb(0);
    fake_tusb_mounted = false;
    TEST_ASSERT(!mboot_usbd_leave_requested());

    // A successful manifest with no reset yet: no leave. The reset after it requests the leave.
    fake_tusb_mounted = true;
    TEST_ASSERT(!mboot_usbd_leave_requested());
    th_manifest_status = DFU_STATUS_OK;
    tud_dfu_manifest_cb(0);
    TEST_ASSERT(!mboot_usbd_leave_requested());
    fake_tusb_mounted = false;
    TEST_ASSERT(mboot_usbd_leave_requested());
    return 0;
}

// ---------------------------------------------------------------------------
// Entry point
// ---------------------------------------------------------------------------

// Runs before test_usbd because of the leave latch.
int test_hooks(void) {
    int failures = 0;
    RUN_TEST(test_60_alt_order_and_read_only_init);
    RUN_TEST(test_61_overlap_rejected);
    RUN_TEST(test_62_read_only_region);
    RUN_TEST(test_63_read_only_part_of_alt);
    RUN_TEST(test_64_session_begin_order);
    RUN_TEST(test_65_session_begin_failure);
    RUN_TEST(test_66_vendor_erase_hook);
    RUN_TEST(test_67_cross_alt_erase_resets_state);
    RUN_TEST(test_68_dnload_status);
    RUN_TEST(test_69_manifest_hook);
    RUN_TEST(test_70_result_request);
    RUN_TEST(test_71_session_ends);
    RUN_TEST(test_72_bus_reset_is_polled);

    // Put the hooks back to their defaults and the alt tracking in mboot_usbd.c
    // at alt 0, which is what test_usbd expects.
    th_begin_rc = 0;
    th_manifest_status = 0;
    th_result_ret = 0;
    tud_dfu_abort_cb(0);
    return failures;
}
