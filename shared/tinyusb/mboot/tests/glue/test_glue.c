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

// test_glue.c - host tests for shared/mcuboot/src/dfu_glue.c on the in-memory
// MCUboot environment in fake_mcuboot.c. Covers the session-begin and manifest
// hooks, confinement of every DFU write and erase to the write ranges,
// read-only regions, the 0x81 result request, status mapping and the session
// loop.
//
// The layout comes from the board and port headers the build is given, see
// fake_mcuboot.h. The slot policy changes the target of a download (the
// secondary slot, or the primary slot for policy single), the session begin
// (trailer and spare sector are only erased for policies with a secondary slot)
// and the pending call at manifest.

#include <errno.h>
#include <setjmp.h>
#include <stdio.h>
#include <string.h>

#include "fake_mcuboot.h"
#include "fake_tusb.h"
#include "flash_map_backend/flash_map_backend.h"
#include "mcuboot_config/mcuboot_config.h"
#include "sysflash/sysflash.h"
#include "tusb.h"
#include "mboot_api.h"
#include "mboot_dfu.h"
#include "mboot_region.h"
#include "mboot_usbd.h"
#include "mcuboot_dfu.h"
#include "mcuboot_types.h"
#include "mcuboot_updatelog.h"
#include "test_main.h"

// fk_wrange_t must match mcuboot_wrange_t.
_Static_assert(sizeof(fk_wrange_t) == sizeof(mcuboot_wrange_t), "layout of mcuboot_wrange_t");
_Static_assert(offsetof(fk_wrange_t, size) == offsetof(mcuboot_wrange_t, size), "layout of mcuboot_wrange_t");
_Static_assert(offsetof(fk_wrange_t, dev) == offsetof(mcuboot_wrange_t, dev), "layout of mcuboot_wrange_t");


void test_fail(const char *file, int line, const char *expr) {
    printf("  FAIL %s:%d: %s\n", file, line, expr);
}

// Target area in device offsets: the slot DFU writes into, spare sector included.
#if MCUBOOT_POLICY_SINGLE
#define AREA_LO FK_PRIMARY_OFF
#define AREA_SIZE FK_PRIMARY_SIZE
#else
#define AREA_LO FK_SECONDARY_OFF
#define AREA_SIZE FK_SECONDARY_SIZE
#endif
#define AREA_HI (AREA_LO + AREA_SIZE)

// Erases done by the session-begin hook: trailer sector(s), then the spare.
#if MCUBOOT_POLICY_SINGLE
#define BEGIN_ERASES 0
#elif FK_SPARE_SIZE != 0
#define BEGIN_ERASES 2
#else
#define BEGIN_ERASES 1
#endif

// View id passed to the validator.
#if FK_SPARE_SIZE != 0
#define VIEW_ID MCUBOOT_AREA_ID_VIEW
#elif MCUBOOT_POLICY_SINGLE
#define VIEW_ID FLASH_AREA_IMAGE_PRIMARY(0)
#else
#define VIEW_ID FLASH_AREA_IMAGE_SECONDARY(0)
#endif

static uint8_t s_data[2048];

static void fill_data(uint8_t v) {
    memset(s_data, v, sizeof(s_data));
}

// DNLOAD through the TinyUSB callback. Returns the DFU status given to TinyUSB.
static uint8_t dnload(uint8_t alt, uint16_t block, uint16_t len) {
    fake_tusb_reset();
    tud_dfu_download_cb(alt, block, s_data, len);
    return fake_tusb_last_finish_status;
}

// Vendor erase request through the control transfer callback (SETUP, DATA, ACK).
// The erase runs in the DATA stage, where a false return stalls the status
// stage; the ACK stage after it cannot fail the request. Returns true if the
// device acknowledged, false if it stalled.
static bool vendor_erase(uint16_t wValue, uint32_t addr, uint32_t length) {
    tusb_control_request_t req = { .bmRequestType = 0x41, .bRequest = 0x80, .wValue = wValue,
                                   .wIndex = 0, .wLength = 8 };
    fake_tusb_reset();
    if (!tud_vendor_control_xfer_cb(0, CONTROL_STAGE_SETUP, &req) || !fake_tusb_last_xfer_called) {
        return false;
    }
    uint8_t *p = fake_tusb_last_xfer_buf;
    for (int i = 0; i < 4; i++) {
        p[i] = (uint8_t)(addr >> (8 * i));
        p[4 + i] = (uint8_t)(length >> (8 * i));
    }
    bool ok = tud_vendor_control_xfer_cb(0, CONTROL_STAGE_DATA, &req);
    (void)tud_vendor_control_xfer_cb(0, CONTROL_STAGE_ACK, &req);
    return ok;
}

// Vendor request 0x81. Returns the number of bytes sent (0 if stalled).
static unsigned vendor_result(uint8_t out[16], uint8_t bmRequestType, uint16_t wLength) {
    tusb_control_request_t req = { .bmRequestType = bmRequestType, .bRequest = 0x81, .wValue = 0,
                                   .wIndex = 0, .wLength = wLength };
    fake_tusb_reset();
    if (!tud_vendor_control_xfer_cb(0, CONTROL_STAGE_SETUP, &req) || !fake_tusb_last_xfer_called) {
        return 0;
    }
    memcpy(out, fake_tusb_last_xfer_buf, fake_tusb_last_xfer_len);
    return fake_tusb_last_xfer_len;
}

static uint32_t le32(const uint8_t *p) {
    return p[0] | ((uint32_t)p[1] << 8) | ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24);
}

static uint16_t le16(const uint8_t *p) {
    return (uint16_t)(p[0] | (p[1] << 8));
}

// True if every flash write and erase is inside the target area.
static bool events_confined(void) {
    for (size_t i = 0; i < fk_event_count; i++) {
        if ((fk_events[i].kind == EV_ERASE || fk_events[i].kind == EV_WRITE) &&
            (fk_events[i].a < AREA_LO || fk_events[i].a + fk_events[i].b > AREA_HI)) {
            return false;
        }
    }
    return true;
}

static size_t flash_ops(void) {
    return fk_count(EV_ERASE) + fk_count(EV_WRITE);
}

#define EV_IS(idx, k, a_, b_) \
    do { \
        TEST_ASSERT((idx) < fk_event_count); \
        TEST_ASSERT_EQ(fk_events[(idx)].kind, (k)); \
        TEST_ASSERT_EQ(fk_events[(idx)].a, (a_)); \
        TEST_ASSERT_EQ(fk_events[(idx)].b, (b_)); \
    } while (0)

// ---------------------------------------------------------------------------
// Table validation
// ---------------------------------------------------------------------------

static void alt_string(uint8_t alt, char *out) {
    uint16_t buf[1 + MBOOT_REGION_DESC_MAX_CONTENT_CHARS];
    int n = mboot_region_get_alt_string(alt, buf, MBOOT_REGION_DESC_MAX_CONTENT_CHARS);
    for (int i = 0; i < n; i++) {
        out[i] = (char)buf[1 + i];
    }
    out[n < 0 ? 0 : n] = '\0';
}

static bool ends_with(const char *s, const char *suffix) {
    size_t a = strlen(s), b = strlen(suffix);
    return a >= b && strcmp(s + a - b, suffix) == 0;
}

static int test_g1_regions_init(int *failures) {
    TEST_ASSERT_EQ(fk_setup(), 0);
    TEST_ASSERT_EQ(mboot_region_count(), 2);
    char s[160];
    alt_string(0, s);
    // The image alt is read/erase/write ('g'), the log alt read-only ('a'). The
    // log is below the image in the address space but is still alt 1.
    TEST_ASSERT(ends_with(s, "*008Kg"));
    alt_string(1, s);
    TEST_ASSERT(ends_with(s, "*008Ka"));
    TEST_ASSERT(strstr(s, "Update log") != NULL);
    return 0;
}

#if !MCUBOOT_POLICY_SINGLE
// The write range for the trailer of the update slot, the last entry in the table.
static fk_wrange_t *trailer_range(void) {
    fk_wrange_t *r = fk_ranges;
    while (r[1].size != 0) {
        r++;
    }
    return r;
}
#endif

static const mboot_region_t k_bad_boot_region[] = {
    { .addr = FK_BASE, .size = FK_BOOT_SIZE, .sector_size = FK_ES, .sector_count = FK_BOOT_SIZE / FK_ES,
      .name = "Boot", .flags = MBOOT_REGION_FLAG_READABLE | MBOOT_REGION_FLAG_ERASE_REQUIRED_BEFORE_WRITE },
};
static const mboot_region_t k_bad_sector_size[] = {
    { .addr = FK_IMAGE_ADDR, .size = FK_IMAGE_SIZE, .sector_size = FK_ES / 2, .sector_count = FK_IMAGE_SIZE / (FK_ES / 2),
      .name = "Image", .flags = MBOOT_REGION_FLAG_READABLE | MBOOT_REGION_FLAG_ERASE_REQUIRED_BEFORE_WRITE },
};
static const mboot_region_t k_off_device[] = {
    { .addr = 0x20000000u, .size = FK_ES, .sector_size = FK_ES, .sector_count = 1,
      .name = "RAM", .flags = MBOOT_REGION_FLAG_READABLE | MBOOT_REGION_FLAG_ERASE_REQUIRED_BEFORE_WRITE },
};
static const mboot_region_t k_image_and_boot_ro[] = {
    // READ_ONLY must not make a region outside the write ranges writable.
    { .addr = FK_BASE, .size = FK_BOOT_SIZE, .sector_size = FK_ES, .sector_count = FK_BOOT_SIZE / FK_ES,
      .name = "Boot", .flags = MBOOT_REGION_FLAG_READABLE | MBOOT_REGION_FLAG_READ_ONLY },
};

static int test_g2_regions_init_rejects(int *failures) {
    // The bootloader as a writable region.
    fk_setup();
    fk_regions = k_bad_boot_region;
    fk_region_count = 1;
    TEST_ASSERT(mcuboot_dfu_regions_init() < 0);

    // Sector size different from the device erase unit.
    fk_setup();
    fk_regions = k_bad_sector_size;
    fk_region_count = 1;
    TEST_ASSERT(mcuboot_dfu_regions_init() < 0);

    // Region on no device.
    fk_setup();
    fk_regions = k_off_device;
    fk_region_count = 1;
    TEST_ASSERT(mcuboot_dfu_regions_init() < 0);

    // Write range smaller than the image region.
    fk_setup();
    fk_ranges[0].size -= FK_ES;
    TEST_ASSERT(mcuboot_dfu_regions_init() < 0);

    // Empty write range table.
    fk_setup();
    memset(fk_ranges, 0, sizeof(fk_ranges));
    TEST_ASSERT(mcuboot_dfu_regions_init() < 0);

    // Write range beyond the device.
    fk_setup();
    fk_ranges[0].size += FK_DEV_SIZE;
    TEST_ASSERT(mcuboot_dfu_regions_init() < 0);

    // Write range not aligned to the erase unit.
    fk_setup();
    fk_ranges[0].addr += 16;
    fk_ranges[0].size -= 16;
    TEST_ASSERT(mcuboot_dfu_regions_init() < 0);

    // Write range naming a device that does not exist.
    fk_setup();
    fk_ranges[0].dev = 3;
    TEST_ASSERT(mcuboot_dfu_regions_init() < 0);

    #if !MCUBOOT_POLICY_SINGLE
    // The data range runs over the update slot trailer, or the trailer range is
    // not session_only. Either would make the trailer writable through the DFU
    // shims.
    fk_setup();
    fk_ranges[0].size += FK_TRAILER_SIZE;
    TEST_ASSERT(mcuboot_dfu_regions_init() < 0);
    fk_setup();
    trailer_range()->session_only = 0;
    TEST_ASSERT(mcuboot_dfu_regions_init() < 0);
    #endif

    // A read-only region is exempt from the writable check. The table is
    // accepted because nothing in it is writable, and writes to it are refused.
    fk_setup();
    fk_regions = k_image_and_boot_ro;
    fk_region_count = 1;
    TEST_ASSERT_EQ(mcuboot_dfu_regions_init(), 0);
    TEST_ASSERT(!mboot_port_flash_is_writable(FK_BASE, 16));
    TEST_ASSERT_EQ(mboot_region_write(FK_BASE, s_data, 16), -EACCES);

    fk_setup();
    return 0;
}

// ---------------------------------------------------------------------------
// Hook order
// ---------------------------------------------------------------------------

static int test_g3_session_begin_order(int *failures) {
    TEST_ASSERT_EQ(fk_setup(), 0);
    fill_data(0x11);
    TEST_ASSERT_EQ(dnload(0, 0, 100), DFU_STATUS_OK);

    size_t i = 0;
    #if !MCUBOOT_POLICY_SINGLE
    // Trailer sector (the last sector of the slot holds the magic), spare sector,
    // DFU_BEGIN, then the core's erase and write of the first data sector.
    EV_IS(i, EV_ERASE, AREA_HI - FK_ES, FK_ES);
    i++;
    #if FK_SPARE_SIZE != 0
    EV_IS(i, EV_ERASE, FK_SECONDARY_OFF, FK_ES);
    i++;
    #endif
    #endif
    EV_IS(i, EV_LOG, LOG_DFU_BEGIN, MCUBOOT_RES_OK);
    i++;
    EV_IS(i, EV_ERASE, FK_IMAGE_OFF, FK_ES);
    i++;
    EV_IS(i, EV_WRITE, FK_IMAGE_OFF, 112);   // 100 bytes padded to the write unit
    i++;
    TEST_ASSERT_EQ(fk_event_count, i);
    TEST_ASSERT_EQ(fk_flash[FK_IMAGE_OFF + 99], 0x11);
    TEST_ASSERT_EQ(fk_flash[FK_IMAGE_OFF + 100], 0xFF);
    TEST_ASSERT_EQ(fk_flash[FK_IMAGE_OFF + 111], 0xFF);

    // Next block of the same session: no hook.
    size_t before = fk_count(EV_LOG);
    TEST_ASSERT_EQ(dnload(0, 1, 2048), DFU_STATUS_OK);
    TEST_ASSERT_EQ(fk_count(EV_LOG), before);

    // DFU_ABORT starts a new session, so the hook runs again on the next block.
    tud_dfu_abort_cb(0);
    TEST_ASSERT_EQ(dnload(0, 0, 16), DFU_STATUS_OK);
    TEST_ASSERT_EQ(fk_count(EV_LOG), before + 1);
    // Two session begins, and the first image sector is erased once per session.
    TEST_ASSERT_EQ(fk_count(EV_ERASE), 2 * BEGIN_ERASES + 2);
    return 0;
}

static int test_g4_begin_failure(int *failures) {
    TEST_ASSERT_EQ(fk_setup(), 0);
    #if MCUBOOT_POLICY_SINGLE
    // Nothing is erased at session begin for policy single.
    TEST_ASSERT_EQ(dnload(0, 0, 16), DFU_STATUS_OK);
    TEST_ASSERT_EQ(fk_count(EV_LOG), 1);
    #else
    // The trailer erase fails. The request fails with errERASE, nothing is
    // written, the failure goes in the log and the 0x81 result, and the next
    // block retries.
    fk_fail_erase_off = AREA_HI - FK_ES;
    TEST_ASSERT_EQ(dnload(0, 0, 16), DFU_STATUS_ERR_ERASE);
    TEST_ASSERT_EQ(fk_count(EV_WRITE), 0);
    size_t i = 0;
    EV_IS(i, EV_ERASE, AREA_HI - FK_ES, FK_ES);
    i++;
    EV_IS(i, EV_LOG, LOG_DFU_BEGIN, MCUBOOT_RES_ERR_FLASH);
    i++;
    TEST_ASSERT_EQ(fk_event_count, i);

    uint8_t r[16];
    TEST_ASSERT_EQ(vendor_result(r, 0xC1, 16), 16);
    TEST_ASSERT(le32(r) >= 1);
    TEST_ASSERT_EQ(le16(r + 4), MCUBOOT_RES_ERR_FLASH);
    TEST_ASSERT_EQ(r[6], SRC_DFU);
    TEST_ASSERT_EQ(r[7], MCUBOOT_DFU_PHASE_BEGIN);
    TEST_ASSERT_EQ(le32(r + 8), AREA_HI - FK_ES);

    fk_fail_erase_off = -1;
    TEST_ASSERT_EQ(dnload(0, 0, 16), DFU_STATUS_OK);
    TEST_ASSERT_EQ(fk_count(EV_WRITE), 1);
    #endif
    return 0;
}

static int test_g5_trailer_sectors(int *failures) {
    #if MCUBOOT_POLICY_SINGLE
    return 0;
    #else
    // A trailer of 2.5 erase units clears three sectors, last first.
    TEST_ASSERT_EQ(fk_setup(), 0);
    fk_trailer_sz = FK_ES * 5 / 2;
    // With that layout the data range ends three sectors before the end of the slot.
    fk_ranges[0].size -= 2 * FK_ES;
    trailer_range()->addr -= 2 * FK_ES;
    trailer_range()->size += 2 * FK_ES;
    TEST_ASSERT_EQ(dnload(0, 0, 16), DFU_STATUS_OK);
    EV_IS(0, EV_ERASE, AREA_HI - 1 * FK_ES, FK_ES);
    EV_IS(1, EV_ERASE, AREA_HI - 2 * FK_ES, FK_ES);
    EV_IS(2, EV_ERASE, AREA_HI - 3 * FK_ES, FK_ES);
    #if FK_SPARE_SIZE != 0
    EV_IS(3, EV_ERASE, FK_SECONDARY_OFF, FK_ES);
    EV_IS(4, EV_LOG, LOG_DFU_BEGIN, MCUBOOT_RES_OK);
    #else
    EV_IS(3, EV_LOG, LOG_DFU_BEGIN, MCUBOOT_RES_OK);
    #endif
    return 0;
    #endif
}

// ---------------------------------------------------------------------------
// Read-only region
// ---------------------------------------------------------------------------

static int test_g6_read_only_alt(int *failures) {
    TEST_ASSERT_EQ(fk_setup(), 0);
    for (uint32_t i = 0; i < FK_LOG_SIZE; i++) {
        fk_flash[FK_LOG_OFF + i] = (uint8_t)(i * 7 + 1);
    }
    uint32_t prot = fk_hash_protected();
    fill_data(0x22);

    // Write to the log alt: address error, no flash access, no hook.
    TEST_ASSERT_EQ(dnload(1, 0, 64), DFU_STATUS_ERR_ADDRESS);
    // Mass and range erase of the log alt stall.
    TEST_ASSERT(!vendor_erase(1, 0, 0xFFFFFFFFu));
    TEST_ASSERT(!vendor_erase(1, FK_ADDR(FK_LOG_OFF), FK_ES));
    TEST_ASSERT_EQ(fk_event_count, 0);
    TEST_ASSERT_EQ(fk_hash_protected(), prot);

    // Upload from the log alt returns its contents.
    uint8_t buf[2048];
    fake_tusb_reset();
    TEST_ASSERT_EQ(tud_dfu_upload_cb(1, 0, buf, sizeof(buf)), 2048);
    TEST_ASSERT(memcmp(buf, &fk_flash[FK_LOG_OFF], 2048) == 0);
    TEST_ASSERT_EQ(fk_event_count, 0);

    // The image alt still works.
    TEST_ASSERT_EQ(dnload(0, 0, 16), DFU_STATUS_OK);
    return 0;
}

// ---------------------------------------------------------------------------
// DFU writes only the target slot
// ---------------------------------------------------------------------------

static int test_g7_dnload_confinement(int *failures) {
    TEST_ASSERT_EQ(fk_setup(), 0);
    uint32_t prot = fk_hash_protected();
    uint16_t last_block = (uint16_t)(FK_IMAGE_SIZE / MBOOT_DFU_XFER_SIZE - 1);
    fill_data(0x33);

    TEST_ASSERT_EQ(dnload(0, last_block, 2048), DFU_STATUS_OK);
    TEST_ASSERT_EQ(fk_flash[FK_IMAGE_OFF + FK_IMAGE_SIZE - 1], 0x33);
    size_t ops = flash_ops();

    // First block past the end of the region, a block far away, and the maximum block.
    TEST_ASSERT_EQ(dnload(0, (uint16_t)(last_block + 1), 2048), DFU_STATUS_ERR_ADDRESS);
    TEST_ASSERT_EQ(dnload(0, 4096, 2048), DFU_STATUS_ERR_ADDRESS);
    TEST_ASSERT_EQ(dnload(0, 65535, 2048), DFU_STATUS_ERR_ADDRESS);
    TEST_ASSERT_EQ(flash_ops(), ops);
    TEST_ASSERT_EQ(fk_hash_protected(), prot);
    TEST_ASSERT(events_confined());
    TEST_ASSERT_EQ(fk_nonblank_writes, 0);

    // Block 0 is the first byte of the region, not of the spare sector.
    fill_data(0x44);
    TEST_ASSERT_EQ(dnload(0, 0, 2048), DFU_STATUS_OK);
    TEST_ASSERT_EQ(fk_flash[FK_IMAGE_OFF], 0x44);
    TEST_ASSERT(events_confined());
    return 0;
}

static int test_g8_shims_refuse_outside(int *failures) {
    TEST_ASSERT_EQ(fk_setup(), 0);
    uint8_t unit[16] = { 0 };
    mboot_addr_t next = 0;
    uint32_t prot = fk_hash_protected();

    // Areas that must never be reachable: boot, seccnt, shadow, FS, log, a gap,
    // the device start and end, and the primary slot unless it is the single slot.
    mboot_addr_t bad[] = {
        FK_ADDR(FK_BOOT_OFF), FK_ADDR(FK_BOOT_OFF + FK_ES),
        FK_ADDR(FK_SECCNT_OFF), FK_ADDR(FK_SHADOW_OFF),
        FK_ADDR(FK_FS_OFF), FK_ADDR(FK_FS_OFF + FK_FS_SIZE - FK_ES),
        FK_ADDR(FK_LOG_OFF), FK_ADDR(FK_LOG_OFF + FK_ES),
        FK_ADDR(AREA_LO - FK_ES),
        FK_ADDR(FK_DEV_SIZE), FK_ADDR(FK_DEV_SIZE - FK_ES),
        0, 0xFFFFFFF0u, FK_BASE - 16,
        #if !MCUBOOT_POLICY_SINGLE
        FK_ADDR(FK_PRIMARY_OFF), FK_ADDR(FK_PRIMARY_OFF + FK_PRIMARY_SIZE - FK_ES),
        #endif
    };
    for (size_t i = 0; i < sizeof(bad) / sizeof(bad[0]); i++) {
        TEST_ASSERT(!mboot_port_flash_is_writable(bad[i], 16));
        TEST_ASSERT_EQ(mboot_port_flash_write(bad[i], unit, 16), -EACCES);
        TEST_ASSERT_EQ(mboot_port_flash_page_erase(bad[i], &next), -EACCES);
    }
    TEST_ASSERT_EQ(fk_event_count, 0);
    TEST_ASSERT_EQ(fk_hash_protected(), prot);

    // A request that starts inside the target and runs past its end is refused.
    TEST_ASSERT(!mboot_port_flash_is_writable(FK_ADDR(AREA_HI - 16), 32));
    TEST_ASSERT_EQ(mboot_port_flash_write(FK_ADDR(AREA_HI - 16), unit, 32), -EACCES);
    TEST_ASSERT(!mboot_port_flash_is_writable(FK_ADDR(AREA_LO), 0));
    TEST_ASSERT(!mboot_port_flash_is_writable(0xFFFFFFF0u, 0x20));   // wraps past 2^32
    TEST_ASSERT_EQ(fk_event_count, 0);

    #if FK_SPARE_SIZE != 0
    // The spare sector and the data region are separate ranges; one request
    // cannot span both.
    TEST_ASSERT(!mboot_port_flash_is_writable(FK_ADDR(FK_SECONDARY_OFF), FK_SPARE_SIZE + 16));
    TEST_ASSERT_EQ(mboot_port_flash_write(FK_ADDR(FK_SECONDARY_OFF + FK_ES - 16), unit, 32), -EACCES);
    TEST_ASSERT_EQ(fk_event_count, 0);
    #endif

    // Inside the target: accepted, erase reports the next sector, misaligned writes fail.
    mboot_addr_t in = FK_IMAGE_ADDR;
    TEST_ASSERT(mboot_port_flash_is_writable(in, FK_IMAGE_SIZE));
    TEST_ASSERT_EQ(mboot_port_flash_page_erase(in + 5, &next), 0);
    TEST_ASSERT_EQ(next, in + FK_ES);
    TEST_ASSERT_EQ(mboot_port_flash_write(in, unit, 16), 0);
    TEST_ASSERT_EQ(mboot_port_flash_write(in + 32, unit, 16), 0);
    TEST_ASSERT_EQ(mboot_port_flash_write(in + 4, unit, 16), -EINVAL);
    TEST_ASSERT_EQ(mboot_port_flash_write(in + 64, unit, 8), -EINVAL);
    TEST_ASSERT(events_confined());

    // Reads are limited to the device, not the write ranges (the log alt is
    // uploaded).
    uint8_t rd[16];
    TEST_ASSERT_EQ(mboot_port_flash_read(FK_ADDR(FK_LOG_OFF), rd, 16), 0);
    TEST_ASSERT_EQ(mboot_port_flash_read(FK_ADDR(FK_DEV_SIZE - 8), rd, 16), -EINVAL);
    TEST_ASSERT_EQ(mboot_port_flash_read(0, rd, 16), -EINVAL);
    return 0;
}

// ---------------------------------------------------------------------------
// Mass and range erase touch only the target slot
// ---------------------------------------------------------------------------

static int test_g9_mass_erase(int *failures) {
    TEST_ASSERT_EQ(fk_setup(), 0);
    memset(fk_flash, 0xA5, sizeof(fk_flash));
    uint32_t prot = fk_hash_protected();

    TEST_ASSERT(vendor_erase(0, 0, 0xFFFFFFFFu));

    // The DFU data region is blank, plus whatever the session-begin hook cleared
    // in the slot. Nothing outside the slot is touched.
    for (uint32_t i = 0; i < FK_IMAGE_SIZE; i++) {
        TEST_ASSERT_EQ(fk_flash[FK_IMAGE_OFF + i], 0xFF);
    }
    TEST_ASSERT_EQ(fk_hash_protected(), prot);
    TEST_ASSERT(events_confined());
    TEST_ASSERT_EQ(fk_count(EV_ERASE), FK_IMAGE_SECTORS + BEGIN_ERASES);
    TEST_ASSERT_EQ(fk_count(EV_WRITE), 0);
    #if FK_SPARE_SIZE != 0
    // The begin hook cleared the spare sector.
    for (uint32_t i = 0; i < FK_ES; i++) {
        TEST_ASSERT_EQ(fk_flash[AREA_LO + i], 0xFF);
    }
    #endif

    // The sectors are marked touched, so the next block does not erase again.
    size_t erases = fk_count(EV_ERASE);
    fill_data(0x55);
    TEST_ASSERT_EQ(dnload(0, 0, 2048), DFU_STATUS_OK);
    TEST_ASSERT_EQ(fk_count(EV_ERASE), erases);
    TEST_ASSERT_EQ(fk_count(EV_WRITE), 1);
    return 0;
}

static int test_g10_range_erase(int *failures) {
    TEST_ASSERT_EQ(fk_setup(), 0);
    memset(fk_flash, 0xA5, sizeof(fk_flash));
    uint32_t prot = fk_hash_protected();

    // Starts outside the region: refused, nothing happens (not even the begin hook).
    mboot_addr_t starts[] = {
        FK_ADDR(FK_BOOT_OFF), FK_ADDR(FK_SECCNT_OFF), FK_ADDR(FK_SHADOW_OFF), FK_ADDR(FK_FS_OFF),
        FK_ADDR(FK_LOG_OFF), FK_ADDR(FK_IMAGE_OFF - FK_ES),
        FK_ADDR(FK_DEV_SIZE), FK_BASE - FK_ES, 0,
        #if !MCUBOOT_POLICY_SINGLE
        FK_ADDR(FK_PRIMARY_OFF), FK_ADDR(FK_PRIMARY_OFF + FK_PRIMARY_SIZE - FK_ES),
        #endif
    };
    for (size_t i = 0; i < sizeof(starts) / sizeof(starts[0]); i++) {
        TEST_ASSERT(!vendor_erase(0, starts[i], FK_ES));
        // A long range that would run into the region.
        TEST_ASSERT(!vendor_erase(0, starts[i], FK_DEV_SIZE));
        TEST_ASSERT_EQ(fk_event_count, 0);
    }
    TEST_ASSERT_EQ(fk_hash_protected(), prot);

    // addr + length wraps around 2^32.
    TEST_ASSERT(!vendor_erase(0, FK_IMAGE_ADDR, 0xFFFFFFF0u));
    TEST_ASSERT(!vendor_erase(0, 0xFFFFF000u, 0x2000));
    TEST_ASSERT_EQ(fk_event_count, 0);

    // Starts in the last sector of the region and runs past the end: that sector
    // is erased, then the request fails. Nothing after the region is touched.
    mboot_addr_t last = FK_IMAGE_ADDR + FK_IMAGE_SIZE - FK_ES;
    TEST_ASSERT(!vendor_erase(0, last, 3 * FK_ES));
    TEST_ASSERT_EQ(fk_flash[FK_IMAGE_OFF + FK_IMAGE_SIZE - 1], 0xFF);
    TEST_ASSERT_EQ(fk_flash[FK_IMAGE_OFF + FK_IMAGE_SIZE - FK_ES - 1], 0xA5);
    TEST_ASSERT_EQ(fk_hash_protected(), prot);
    TEST_ASSERT(events_confined());
    // The begin hook erases plus the one data sector.
    TEST_ASSERT_EQ(fk_count(EV_ERASE), BEGIN_ERASES + 1);

    // A range inside the region erases the sectors covering it.
    fk_setup();
    memset(fk_flash, 0xA5, sizeof(fk_flash));
    TEST_ASSERT(vendor_erase(0, FK_IMAGE_ADDR + FK_ES + 100, FK_ES));   // straddles two sectors
    TEST_ASSERT_EQ(fk_flash[FK_IMAGE_OFF + FK_ES - 1], 0xA5);
    TEST_ASSERT_EQ(fk_flash[FK_IMAGE_OFF + FK_ES], 0xFF);
    TEST_ASSERT_EQ(fk_flash[FK_IMAGE_OFF + 3 * FK_ES - 1], 0xFF);
    TEST_ASSERT_EQ(fk_flash[FK_IMAGE_OFF + 3 * FK_ES], 0xA5);
    TEST_ASSERT_EQ(fk_hash_protected(), prot);

    // A wrong alt index stalls.
    TEST_ASSERT(!vendor_erase(7, FK_IMAGE_ADDR, FK_ES));
    return 0;
}

// ---------------------------------------------------------------------------
// Manifest, validation result and status mapping
// ---------------------------------------------------------------------------

static const struct {
    uint16_t code;
    uint8_t status;
} k_status_map[] = {
    { MCUBOOT_RES_ERR_HASH, DFU_STATUS_ERR_FILE },
    { MCUBOOT_RES_ERR_SIG, DFU_STATUS_ERR_FILE },
    { MCUBOOT_RES_ERR_DOWNGRADE, DFU_STATUS_ERR_FILE },
    { MCUBOOT_RES_ERR_NOT_TARGET, DFU_STATUS_ERR_FILE },
    { MCUBOOT_RES_ERR_LAYOUT, DFU_STATUS_ERR_FILE },
    { MCUBOOT_RES_ERR_HEADER, DFU_STATUS_ERR_ADDRESS },
    { MCUBOOT_RES_ERR_TOO_BIG, DFU_STATUS_ERR_ADDRESS },
    { MCUBOOT_RES_ERR_FLASH, DFU_STATUS_ERR_WRITE },
    { MCUBOOT_RES_ERR_NO_IMAGE, DFU_STATUS_ERR_UNKNOWN },
};

static int test_g11_manifest_rejected(int *failures) {
    for (size_t k = 0; k < sizeof(k_status_map) / sizeof(k_status_map[0]); k++) {
        TEST_ASSERT_EQ(fk_setup(), 0);
        fill_data(0x66);
        TEST_ASSERT_EQ(dnload(0, 0, 2048), DFU_STATUS_OK);
        fk_event_count = 0;
        uint8_t r[16];
        TEST_ASSERT_EQ(vendor_result(r, 0xC1, 16), 16);
        uint32_t seq_begin = le32(r);

        fk_validate_result.code = k_status_map[k].code;
        fk_validate_result.detail = 0x1234 + (uint32_t)k;
        fake_tusb_reset();
        tud_dfu_manifest_cb(0);
        TEST_ASSERT(fake_tusb_last_finish_called);
        TEST_ASSERT_EQ(fake_tusb_last_finish_status, k_status_map[k].status);

        // Validated once with every check, never marked pending, logged as
        // rejected, and the header sector erased so the image cannot linger.
        size_t i = 0;
        EV_IS(i, EV_VALIDATE, VIEW_ID, VALIDATE_FULL | VALIDATE_CHECK_TARGET | VALIDATE_CHECK_DOWNGRADE);
        i++;
        EV_IS(i, EV_LOG, LOG_IMAGE_REJECTED, k_status_map[k].code);
        i++;
        EV_IS(i, EV_ERASE, FK_IMAGE_OFF, FK_ES);
        i++;
        TEST_ASSERT_EQ(fk_event_count, i);
        TEST_ASSERT_EQ(fk_count(EV_PENDING), 0);
        TEST_ASSERT_EQ(fk_flash[FK_IMAGE_OFF], 0xFF);

        // The result request reports the rejection.
        TEST_ASSERT_EQ(vendor_result(r, 0xC1, 16), 16);
        TEST_ASSERT_EQ(le32(r), seq_begin + 1);
        TEST_ASSERT_EQ(le16(r + 4), k_status_map[k].code);
        TEST_ASSERT_EQ(r[6], SRC_DFU);
        TEST_ASSERT_EQ(r[7], MCUBOOT_DFU_PHASE_VALIDATE);
        TEST_ASSERT_EQ(le32(r + 8), 0x1234 + (uint32_t)k);
        TEST_ASSERT_EQ(le32(r + 12), 0);

        // A rejected download does not request the leave; a bus reset keeps DFU running.
        mboot_usbd_bus_reset();
        TEST_ASSERT(!mboot_usbd_leave_requested());
    }
    return 0;
}

static int test_g12_pending_failure(int *failures) {
    #if MCUBOOT_POLICY_SINGLE
    return 0;
    #else
    TEST_ASSERT_EQ(fk_setup(), 0);
    fill_data(0x66);
    TEST_ASSERT_EQ(dnload(0, 0, 2048), DFU_STATUS_OK);
    fk_event_count = 0;
    fk_pending_rc = -5;
    fake_tusb_reset();
    tud_dfu_manifest_cb(0);
    TEST_ASSERT_EQ(fake_tusb_last_finish_status, DFU_STATUS_ERR_WRITE);
    TEST_ASSERT_EQ(fk_count(EV_PENDING), 1);
    size_t i = 2;
    EV_IS(i, EV_LOG, LOG_IMAGE_REJECTED, MCUBOOT_RES_ERR_PENDING);
    // The image was valid, so its header is not erased.
    TEST_ASSERT_EQ(fk_count(EV_ERASE), 0);
    TEST_ASSERT_EQ(fk_flash[FK_IMAGE_OFF], 0x66);
    uint8_t r[16];
    TEST_ASSERT_EQ(vendor_result(r, 0xC1, 16), 16);
    TEST_ASSERT_EQ(le16(r + 4), MCUBOOT_RES_ERR_PENDING);
    TEST_ASSERT_EQ(r[7], MCUBOOT_DFU_PHASE_PENDING);
    TEST_ASSERT_EQ(le32(r + 8), (uint32_t)-5);
    mboot_usbd_bus_reset();
    TEST_ASSERT(!mboot_usbd_leave_requested());
    return 0;
    #endif
}

static int test_g13_result_request(int *failures) {
    TEST_ASSERT_EQ(fk_setup(), 0);
    uint8_t r[16];
    // The reply is 16 bytes. The next outcome is the session begin of a download.
    TEST_ASSERT_EQ(vendor_result(r, 0xC1, 16), 16);
    uint32_t seq = le32(r);
    TEST_ASSERT_EQ(dnload(0, 0, 16), DFU_STATUS_OK);
    TEST_ASSERT_EQ(vendor_result(r, 0xC1, 16), 16);
    TEST_ASSERT_EQ(le32(r), seq + 1);
    TEST_ASSERT_EQ(le16(r + 4), MCUBOOT_RES_OK);
    TEST_ASSERT_EQ(r[6], SRC_DFU);
    TEST_ASSERT_EQ(r[7], MCUBOOT_DFU_PHASE_BEGIN);
    TEST_ASSERT_EQ(le32(r + 12), 0);
    // A wLength other than 16, or the wrong direction, stalls.
    TEST_ASSERT_EQ(vendor_result(r, 0xC1, 8), 0);
    TEST_ASSERT_EQ(vendor_result(r, 0xC1, 64), 0);
    TEST_ASSERT_EQ(vendor_result(r, 0x41, 16), 0);
    return 0;
}

static int test_g14_target_view(int *failures) {
    TEST_ASSERT_EQ(fk_setup(), 0);
    const struct flash_area *v = mcuboot_dfu_target_view();
    TEST_ASSERT_NOTNULL(v);
    TEST_ASSERT_EQ(v->fa_id, VIEW_ID);
    TEST_ASSERT_EQ(v->fa_off, FK_IMAGE_OFF);
    // The slot without its spare sector: the DFU data region and the trailer.
    TEST_ASSERT_EQ(v->fa_size, FK_IMAGE_SIZE + FK_TRAILER_SIZE);
    TEST_ASSERT_EQ(v->fa_device_id, 0);
    TEST_ASSERT_EQ(mboot_region_active_base(), FK_BASE + v->fa_off);
    TEST_ASSERT_EQ(mboot_region_active_size(), v->fa_size - FK_TRAILER_SIZE);
    return 0;
}

// ---------------------------------------------------------------------------
// Session loop
// ---------------------------------------------------------------------------

#define LOOP_RESET 1
#define LOOP_STOP 2

// Run mcuboot_dfu_run() until it resets (returns LOOP_RESET) or a task hook stops
// it (LOOP_STOP).
static int run_loop(mcuboot_recovery_cause_t why) {
    int r = setjmp(fk_reset_jmp);
    if (r == 0) {
        mcuboot_dfu_run(why);
    }
    return r;
}

static unsigned s_stop_at;
static unsigned s_dnload_at;
static unsigned s_upload_at;
static unsigned s_abort_at;
static unsigned s_manifest_at;
static unsigned s_unmount_at;

static void loop_hook(unsigned call) {
    uint8_t buf[64];
    if (call == s_dnload_at) {
        dnload(0, 0, 16);
    }
    if (s_upload_at != 0 && (call == s_upload_at || call == s_upload_at + 100)) {
        fake_tusb_reset();
        tud_dfu_upload_cb(1, 0, buf, sizeof(buf));
    }
    if (s_manifest_at != 0 && call == s_manifest_at) {
        fake_tusb_reset();
        tud_dfu_manifest_cb(0);
    }
    if (s_unmount_at != 0) {
        fake_tusb_mounted = call < s_unmount_at;
    }
    if (call == s_abort_at) {
        tud_dfu_abort_cb(0);
    }
    if (call == s_stop_at) {
        longjmp(fk_reset_jmp, LOOP_STOP);
    }
}

static void loop_setup(unsigned stop, unsigned dnload_at, unsigned upload_at, unsigned abort_at) {
    fk_setup();
    fill_data(0x77);
    fk_tick_step = 1000;
    s_stop_at = stop;
    s_dnload_at = dnload_at;
    s_upload_at = upload_at;
    s_abort_at = abort_at;
    s_manifest_at = 0;
    s_unmount_at = 0;
    fake_tusb_mounted = false;
    fk_task_hook = loop_hook;
}

static int test_g15_timeout(int *failures) {
    // Forced entry with an idle host resets after MCUBOOT_DFU_TIMEOUT_S seconds,
    // after tearing down the bootloader state.
    loop_setup(100000, 0, 0, 0);
    TEST_ASSERT_EQ(run_loop(REC_FORCED), LOOP_RESET);
    TEST_ASSERT_EQ(fk_task_calls, MCUBOOT_DFU_TIMEOUT_S);
    TEST_ASSERT_EQ(fk_event_count, 2);
    TEST_ASSERT_EQ(fk_events[0].kind, EV_DEINIT);
    TEST_ASSERT_EQ(fk_events[1].kind, EV_RESET);

    loop_setup(100000, 0, 0, 0);
    TEST_ASSERT_EQ(run_loop(REC_APP_REQUEST), LOOP_RESET);
    TEST_ASSERT_EQ(fk_task_calls, MCUBOOT_DFU_TIMEOUT_S);

    // Recovery from a missing or failed image never times out.
    mcuboot_recovery_cause_t no_timeout[] = { REC_NO_IMAGE, REC_FSLOAD_FAILED, REC_FAULT };
    for (size_t i = 0; i < sizeof(no_timeout) / sizeof(no_timeout[0]); i++) {
        loop_setup(2 * MCUBOOT_DFU_TIMEOUT_S + 50, 0, 0, 0);
        TEST_ASSERT_EQ(run_loop(no_timeout[i]), LOOP_STOP);
        TEST_ASSERT_EQ(fk_count(EV_RESET), 0);
    }

    // DFU activity (an upload from the log alt) restarts the idle timer.
    loop_setup(100000, 0, 100, 0);
    TEST_ASSERT_EQ(run_loop(REC_FORCED), LOOP_RESET);
    // Last activity at call 200 (the hook's second upload), then the full timeout.
    TEST_ASSERT_EQ(fk_task_calls, 200 + MCUBOOT_DFU_TIMEOUT_S);

    // An open write session suppresses the timeout. An abort ends the session
    // and the idle timer restarts from the abort.
    loop_setup(3 * MCUBOOT_DFU_TIMEOUT_S, 10, 0, 0);
    TEST_ASSERT_EQ(run_loop(REC_FORCED), LOOP_STOP);
    TEST_ASSERT_EQ(fk_count(EV_RESET), 0);

    loop_setup(100000, 10, 0, 20);
    TEST_ASSERT_EQ(run_loop(REC_FORCED), LOOP_RESET);
    TEST_ASSERT_EQ(fk_task_calls, 20 + MCUBOOT_DFU_TIMEOUT_S);

    // A bus reset ends an open session that has been idle for longer than the
    // timeout. It counts as activity, so the timeout restarts from the reset and
    // the bootloader does not reset when the session ends.
    loop_setup(100000, 10, 0, 0);
    s_unmount_at = 3 * MCUBOOT_DFU_TIMEOUT_S;
    TEST_ASSERT_EQ(run_loop(REC_FORCED), LOOP_RESET);
    TEST_ASSERT_EQ(fk_task_calls, 3 * MCUBOOT_DFU_TIMEOUT_S + MCUBOOT_DFU_TIMEOUT_S);
    return 0;
}

static int test_g16_init_failure(int *failures) {
    // Inconsistent tables end in a reset without running the USB loop.
    loop_setup(100000, 0, 0, 0);
    memset(fk_ranges, 0, sizeof(fk_ranges));
    TEST_ASSERT_EQ(run_loop(REC_FORCED), LOOP_RESET);
    TEST_ASSERT_EQ(fk_task_calls, 0);
    TEST_ASSERT_EQ(fk_events[fk_event_count - 1].kind, EV_RESET);
    TEST_ASSERT_EQ(fk_events[fk_event_count - 2].kind, EV_DEINIT);
    return 0;
}

// Has to run after every test that needs the leave request clear: the core
// latches the request once a manifest has succeeded and the bus has been reset.
static int test_g17_manifest_accepted_and_leave(int *failures) {
    TEST_ASSERT_EQ(fk_setup(), 0);
    fill_data(0x88);
    TEST_ASSERT_EQ(dnload(0, 0, 2048), DFU_STATUS_OK);
    fk_event_count = 0;
    fake_tusb_reset();
    tud_dfu_manifest_cb(0);
    TEST_ASSERT_EQ(fake_tusb_last_finish_status, DFU_STATUS_OK);

    size_t i = 0;
    EV_IS(i, EV_VALIDATE, VIEW_ID, VALIDATE_FULL | VALIDATE_CHECK_TARGET | VALIDATE_CHECK_DOWNGRADE);
    i++;
    #if MCUBOOT_POLICY_SWAP
    // Swap: reverts unless the application confirms.
    EV_IS(i, EV_PENDING, 0, 0);
    i++;
    #elif MCUBOOT_POLICY_OVERWRITE_EXTERNAL
    // Overwrite-only cannot revert, so it is permanent.
    EV_IS(i, EV_PENDING, 0, 1);
    i++;
    #endif
    EV_IS(i, EV_LOG, LOG_IMAGE_ACCEPTED, MCUBOOT_RES_OK);
    i++;
    TEST_ASSERT_EQ(fk_event_count, i);   // header not erased, nothing else touched
    TEST_ASSERT_EQ(fk_flash[FK_IMAGE_OFF], 0x88);
    uint8_t r[16];
    TEST_ASSERT_EQ(vendor_result(r, 0xC1, 16), 16);
    TEST_ASSERT_EQ(le16(r + 4), MCUBOOT_RES_OK);
    TEST_ASSERT_EQ(r[7], MCUBOOT_DFU_PHASE_VALIDATE);

    // Manifest of the read-only log alt does nothing.
    fk_event_count = 0;
    fake_tusb_reset();
    tud_dfu_manifest_cb(1);
    TEST_ASSERT_EQ(fake_tusb_last_finish_status, DFU_STATUS_OK);
    TEST_ASSERT_EQ(fk_event_count, 0);

    // After the manifest and the bus reset the session loop leaves by reset. The
    // loop initialises the core, so the download, manifest and reset all happen
    // inside it: the device is configured for calls 1-7 and not from call 8.
    loop_setup(100000, 3, 0, 0);
    fill_data(0x88);
    s_manifest_at = 5;
    s_unmount_at = 8;
    TEST_ASSERT_EQ(run_loop(REC_NO_IMAGE), LOOP_RESET);
    TEST_ASSERT_EQ(fk_task_calls, 8);
    TEST_ASSERT_EQ(fk_events[fk_event_count - 2].kind, EV_DEINIT);
    TEST_ASSERT_EQ(fk_events[fk_event_count - 1].kind, EV_RESET);
    return 0;
}

// The update slot trailer holds the swap state. DFU must not be able to write or
// erase it, whether by a block, a vendor erase or the flash shims. Only the
// session begin erases it.
static int test_g18_trailer_confinement(int *failures) {
    #if MCUBOOT_POLICY_SINGLE
    return 0;
    #else
    TEST_ASSERT_EQ(fk_setup(), 0);
    const uint32_t trailer_off = FK_SECONDARY_OFF + FK_SECONDARY_SIZE - FK_TRAILER_SIZE;
    const mboot_addr_t trailer = FK_ADDR(trailer_off);
    // Fill the trailer with a pattern so a change shows up.
    memset(&fk_flash[trailer_off], 0x5A, FK_TRAILER_SIZE);
    uint32_t before = fk_hash(trailer_off, FK_TRAILER_SIZE);
    uint8_t unit[16] = { 0 };
    mboot_addr_t next = 0;

    // The flash shims refuse the trailer, whole or in part, and a request
    // spanning the data range and the trailer.
    TEST_ASSERT(!mboot_port_flash_is_writable(trailer, 16));
    TEST_ASSERT(!mboot_port_flash_is_writable(trailer + FK_TRAILER_SIZE - 16, 16));
    TEST_ASSERT(!mboot_port_flash_is_writable(trailer - 16, 32));
    TEST_ASSERT_EQ(mboot_port_flash_write(trailer, unit, 16), -EACCES);
    TEST_ASSERT_EQ(mboot_port_flash_write(trailer + FK_TRAILER_SIZE - 16, unit, 16), -EACCES);
    TEST_ASSERT_EQ(mboot_port_flash_write(trailer - 16, unit, 32), -EACCES);
    TEST_ASSERT_EQ(mboot_port_flash_page_erase(trailer, &next), -EACCES);
    #if FK_SPARE_SIZE != 0
    TEST_ASSERT_EQ(mboot_port_flash_write(FK_ADDR(FK_SECONDARY_OFF), unit, 16), -EACCES);
    TEST_ASSERT_EQ(mboot_port_flash_page_erase(FK_ADDR(FK_SECONDARY_OFF), &next), -EACCES);
    #endif
    TEST_ASSERT_EQ(fk_event_count, 0);

    // Blocks past the end of the data region address the trailer and are refused.
    uint16_t first_trailer_block = (uint16_t)(FK_IMAGE_SIZE / MBOOT_DFU_XFER_SIZE);
    fill_data(0x77);
    for (uint16_t b = first_trailer_block; b < first_trailer_block + FK_TRAILER_SIZE / MBOOT_DFU_XFER_SIZE; b++) {
        TEST_ASSERT_EQ(dnload(0, b, 2048), DFU_STATUS_ERR_ADDRESS);
    }
    TEST_ASSERT_EQ(fk_event_count, 0);

    // Range erases that start in the trailer.
    TEST_ASSERT(!vendor_erase(0, trailer, FK_ES));
    TEST_ASSERT(!vendor_erase(0, trailer + 100, FK_ES));
    TEST_ASSERT_EQ(fk_hash(trailer_off, FK_TRAILER_SIZE), before);
    size_t trailer_erases = 0;
    for (size_t i = 0; i < fk_event_count; i++) {
        if (fk_events[i].kind == EV_ERASE && fk_events[i].a >= trailer_off) {
            trailer_erases++;
        }
    }
    TEST_ASSERT_EQ(trailer_erases, 0);

    // A mass erase stops at the end of the data region.
    fk_setup();
    memset(&fk_flash[trailer_off], 0x5A, FK_TRAILER_SIZE);
    TEST_ASSERT(vendor_erase(0, 0, 0xFFFFFFFFu));
    TEST_ASSERT_EQ(fk_count(EV_ERASE), FK_IMAGE_SECTORS + BEGIN_ERASES);
    // The only erase of the trailer is the one at session begin, before anything else.
    EV_IS(0, EV_ERASE, trailer_off, FK_TRAILER_SIZE);
    return 0;
    #endif
}

int main(void) {
    int failures = 0;

    RUN_TEST(test_g1_regions_init);
    RUN_TEST(test_g2_regions_init_rejects);
    RUN_TEST(test_g3_session_begin_order);
    RUN_TEST(test_g4_begin_failure);
    RUN_TEST(test_g5_trailer_sectors);
    RUN_TEST(test_g6_read_only_alt);
    RUN_TEST(test_g7_dnload_confinement);
    RUN_TEST(test_g8_shims_refuse_outside);
    RUN_TEST(test_g9_mass_erase);
    RUN_TEST(test_g10_range_erase);
    RUN_TEST(test_g11_manifest_rejected);
    RUN_TEST(test_g12_pending_failure);
    RUN_TEST(test_g13_result_request);
    RUN_TEST(test_g14_target_view);
    RUN_TEST(test_g15_timeout);
    RUN_TEST(test_g16_init_failure);
    RUN_TEST(test_g17_manifest_accepted_and_leave);
    RUN_TEST(test_g18_trailer_confinement);

    if (failures == 0) {
        printf("GLUE TESTS PASSED\n");
        return 0;
    }
    printf("GLUE TOTAL FAILURES: %d\n", failures);
    return 1;
}
