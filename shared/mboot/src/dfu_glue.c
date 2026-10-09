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

// DFU front end of the bootloader: the mboot_port_* shims and DFU hooks that bind
// shared/mboot/dfu to the flash map and bootutil.

#include <errno.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <string.h>

#include "flash_map_backend/flash_map_backend.h"
#include "mcuboot_config/mcuboot_config.h"
#include "mcuboot_config/mcuboot_logging.h"
#include "sysflash/sysflash.h"
#include "tusb.h"

#include "mboot_api.h"
#include "mboot_dfu.h"
#include "mboot_region.h"
#include "mboot_usbd.h"

#include "mboot_dfu_recovery.h"
#include "mboot_port.h"
#include "mboot_types.h"
#include "mboot_update.h"
#include "mboot_validate.h"

#ifndef MBOOT_DFU_TIMEOUT_S
#define MBOOT_DFU_TIMEOUT_S 120
#endif

// Bits of mboot_port_led().
#define LED_ALIVE (1u << 0)
#define LED_USB (1u << 1)
#define LED_ERROR (1u << 2)

// ---------------------------------------------------------------------------
// Write ranges and the mboot_port_flash_* shims
// ---------------------------------------------------------------------------

// The range of mboot_write_ranges[] that contains all of [addr, addr + len), or NULL.
// A request spanning two ranges is refused. A session_only range is found only when
// with_session_only is set: the DFU flash shims never write or erase it, only the session begin
// does (through the flash map).
static const mboot_wrange_t *wrange_find(mboot_addr_t addr, size_t len, bool with_session_only) {
    if (len == 0) {
        return NULL;
    }
    for (const mboot_wrange_t *r = mboot_write_ranges; r->size != 0; r++) {
        if (r->session_only && !with_session_only) {
            continue;
        }
        if (addr >= r->addr && len <= r->size && addr - r->addr <= r->size - len) {
            return r;
        }
    }
    return NULL;
}

#if !MBOOT_POLICY_SINGLE
// True if [addr, addr + len) intersects a range that the DFU flash shims may write.
static bool wrange_writable_overlaps(mboot_addr_t addr, uint32_t len) {
    for (const mboot_wrange_t *r = mboot_write_ranges; r->size != 0; r++) {
        if (!r->session_only && addr < r->addr + r->size && r->addr < addr + len) {
            return true;
        }
    }
    return false;
}
#endif

static uint32_t wrange_dev_off(const mboot_wrange_t *r, mboot_addr_t addr) {
    return addr - mboot_devs[r->dev].base;
}

bool mboot_port_flash_is_writable(mboot_addr_t addr, size_t len) {
    return wrange_find(addr, len, false) != NULL;
}

// Erase the sector that contains addr, if it lies inside a write range. Used by the
// mboot_port_flash_page_erase() shim and by the session-begin hook.
static int glue_erase_sector(mboot_addr_t addr, mboot_addr_t *next_addr) {
    const mboot_wrange_t *r = wrange_find(addr, 1, false);
    if (r == NULL) {
        return -EACCES;
    }
    const mboot_flash_dev_t *d = &mboot_devs[r->dev];
    uint32_t off = wrange_dev_off(r, addr);
    uint32_t unit = mboot_dev_erase_at(d, off);
    if (unit == 0) {
        return -EACCES;
    }
    off -= off % unit;
    mboot_addr_t start = d->base + off;
    if (wrange_find(start, unit, false) == NULL) {
        return -EACCES;
    }
    mboot_port_wdt_feed();
    int rc = mboot_flash_dev_erase(r->dev, off, unit);
    if (rc != 0) {
        return rc;
    }
    *next_addr = start + unit;
    return 0;
}

int mboot_port_flash_page_erase(mboot_addr_t addr, mboot_addr_t *next_addr) {
    return glue_erase_sector(addr, next_addr);
}

int mboot_port_flash_write(mboot_addr_t addr, const uint8_t *src, size_t len) {
    const mboot_wrange_t *r = wrange_find(addr, len, false);
    if (r == NULL) {
        return -EACCES;
    }
    const mboot_flash_dev_t *d = &mboot_devs[r->dev];
    uint32_t off = wrange_dev_off(r, addr);
    if (off % d->write_unit != 0 || len % d->write_unit != 0) {
        return -EINVAL;
    }
    mboot_port_wdt_feed();
    return mboot_flash_dev_write(r->dev, off, src, (uint32_t)len);
}

// Reads are confined by the region table (mboot_region_read() checks the active alt
// setting and its READABLE flag); this shim only needs to find the device.
int mboot_port_flash_read(mboot_addr_t addr, uint8_t *dst, size_t len) {
    for (unsigned i = 0; i < mboot_dev_count; i++) {
        const mboot_flash_dev_t *d = &mboot_devs[i];
        if (addr >= d->base && len <= d->size && addr - d->base <= d->size - len) {
            return mboot_flash_dev_read((uint8_t)i, addr - d->base, dst, (uint32_t)len);
        }
    }
    return -EINVAL;
}

// ---------------------------------------------------------------------------
// USB identity and UI shims
// ---------------------------------------------------------------------------

void mboot_port_get_serial_number(char *buf, size_t buf_len) {
    mboot_port_usb_serial_number(buf, buf_len);
}

const char *mboot_port_get_product_string(void) {
    return MBOOT_DFU_PRODUCT;
}

uint16_t mboot_port_get_vid(void) {
    return MBOOT_DFU_VID;
}

uint16_t mboot_port_get_pid(void) {
    return MBOOT_DFU_PID;
}

// ---------------------------------------------------------------------------
// Table validation
// ---------------------------------------------------------------------------

static const mboot_flash_dev_t *dev_containing(mboot_addr_t addr, mboot_addr_t size) {
    for (unsigned i = 0; i < mboot_dev_count; i++) {
        const mboot_flash_dev_t *d = &mboot_devs[i];
        if (addr >= d->base && size <= d->size && addr - d->base <= d->size - size) {
            return d;
        }
    }
    return NULL;
}

int mboot_dfu_recovery_regions_init(void) {
    // The core pads blocks to the value it was compiled with; it must be the one of the layout.
    if (mboot_dfu_write_align != MBOOT_DFU_WRITE_ALIGN) {
        return -EINVAL;
    }
    if (mboot_write_ranges[0].size == 0) {
        return -EINVAL;
    }
    for (const mboot_wrange_t *r = mboot_write_ranges; r->size != 0; r++) {
        if (r->dev >= mboot_dev_count) {
            return -EINVAL;
        }
        const mboot_flash_dev_t *d = &mboot_devs[r->dev];
        if (dev_containing(r->addr, r->size) != d) {
            return -EINVAL;
        }
        uint32_t off = r->addr - d->base;
        uint32_t unit = mboot_dev_erase_at(d, off);
        if (unit == 0 || off % unit != 0 || r->size % unit != 0 || r->size > mboot_dev_run_end(d, off) - off) {
            return -EINVAL;
        }
        // The core pads every block to MBOOT_DFU_WRITE_ALIGN; the device needs whole
        // write units.
        if (MBOOT_DFU_WRITE_ALIGN % d->write_unit != 0) {
            return -EINVAL;
        }
    }

    // Every region must sit on a device, in one run, and use its erase unit as sector size.
    const mboot_region_t *regions;
    size_t count;
    mboot_port_get_regions(&regions, &count);
    for (size_t i = 0; i < count; i++) {
        const mboot_region_t *g = &regions[i];
        const mboot_flash_dev_t *d = dev_containing(g->addr, g->size);
        uint32_t unit = d != NULL ? mboot_dev_erase_at(d, g->addr - d->base) : 0;
        if (unit == 0 || g->sector_size != unit || (g->addr - d->base) % unit != 0
            || g->size > mboot_dev_run_end(d, g->addr - d->base) - (g->addr - d->base)) {
            return -EINVAL;
        }
    }

    // The trailer of the update slot (swap state) is erased by the session begin only, never
    // written through the DFU shims.
    #if !MBOOT_POLICY_SINGLE
    mboot_update_target_t t;
    if (mboot_update_target(&t) != 0) {
        return -EINVAL;
    }
    mboot_addr_t trailer = mboot_devs[t.fap->fa_device_id].base + t.fap->fa_off + t.trailer_off;
    if (wrange_writable_overlaps(trailer, t.fap->fa_size - t.trailer_off)) {
        return -EINVAL;
    }
    #endif

    // mboot_region_init() asks mboot_port_flash_is_writable() about every region that
    // is not read-only, so such a region outside the write ranges is rejected there.
    return mboot_region_init();
}

// ---------------------------------------------------------------------------
// Target area
// ---------------------------------------------------------------------------

const struct flash_area *mboot_dfu_recovery_target_view(void) {
    static struct flash_area view;
    static bool built;

    mboot_update_target_t t;
    if (mboot_update_target(&t) != 0) {
        return NULL;
    }
    if (t.update_off == 0) {
        return t.fap;
    }
    // The first sector of the slot is not part of the DFU region and not part of the image.
    if (!built) {
        view = *t.fap;
        view.fa_id = MBOOT_AREA_ID_VIEW;
        view.fa_off += t.update_off;
        view.fa_size -= t.update_off;
        built = true;
    }
    return &view;
}

// ---------------------------------------------------------------------------
// Last result (vendor request 0x81)
// ---------------------------------------------------------------------------

static mboot_dfu_recovery_result_t s_result;

static void set_last_result(uint16_t code, uint8_t phase, uint32_t detail) {
    s_result.seq++;
    s_result.code = code;
    s_result.source = MBOOT_DFU_RESULT_SOURCE_DFU;
    s_result.phase = phase;
    s_result.detail = detail;
    s_result.reserved = 0;
}

static void put_le16(uint8_t *p, uint16_t v) {
    p[0] = (uint8_t)v;
    p[1] = (uint8_t)(v >> 8);
}

static void put_le32(uint8_t *p, uint32_t v) {
    put_le16(p, (uint16_t)v);
    put_le16(p + 2, (uint16_t)(v >> 16));
}

uint16_t mboot_hook_get_result(uint8_t *buf, uint16_t len) {
    if (len < MBOOT_DFU_RESULT_WIRE_LEN) {
        return 0;
    }
    put_le32(buf, s_result.seq);
    put_le16(buf + 4, s_result.code);
    buf[6] = s_result.source;
    buf[7] = s_result.phase;
    put_le32(buf + 8, s_result.detail);
    put_le32(buf + 12, s_result.reserved);
    return MBOOT_DFU_RESULT_WIRE_LEN;
}

// ---------------------------------------------------------------------------
// Hooks
// ---------------------------------------------------------------------------

// True if alt setting alt covers flash that DFU may write: its first sector lies inside
// a write range.
static bool alt_is_image(uint8_t alt) {
    uint32_t cookie = 0;
    mboot_addr_t addr;
    uint32_t size;
    if (!mboot_region_sector_iter(alt, &cookie, &addr, &size)) {
        return false;
    }
    return wrange_find(addr, size, false) != NULL;
}

#if !MBOOT_POLICY_SINGLE
// Clears the state the next boot would act on: the secondary slot trailer, then the spare
// sector of swap using offset (the shared session begin of mboot_update.h). The erases go
// through flash_area_erase(), not the DFU shims, so the spans are checked against the write
// ranges first. Both must be session_only ranges, so a damaged table can't widen what a DFU
// session may erase.
static int prepare_secondary(uint32_t *fail_off) {
    mboot_update_target_t t;
    *fail_off = 0;
    int rc = mboot_update_target(&t);
    if (rc != 0) {
        return rc;
    }
    mboot_addr_t base = mboot_devs[t.fap->fa_device_id].base + t.fap->fa_off;
    if (wrange_find(base + t.trailer_off, t.fap->fa_size - t.trailer_off, true) == NULL
        || (t.update_off != 0 && wrange_find(base, t.update_off, true) == NULL)) {
        *fail_off = t.fap->fa_off + t.trailer_off;
        return -EACCES;
    }
    return mboot_update_begin(&t, fail_off);
}
#endif

// First write or erase of a session on a writable alt setting. Prepares the update slot
// (trailer and spare sector erased, for the policies that have a secondary slot).
int mboot_hook_session_begin(uint8_t alt) {
    if (!alt_is_image(alt)) {
        return 0;
    }
    uint16_t code = MBOOT_RES_OK;
    uint32_t detail = 0;
    int rc = 0;
    #if !MBOOT_POLICY_SINGLE
    rc = prepare_secondary(&detail);
    if (rc != 0) {
        code = MBOOT_RES_ERR_FLASH;
    }
    #endif
    set_last_result(code, MBOOT_DFU_PHASE_BEGIN, detail);
    if (rc != 0) {
        MCUBOOT_LOG_ERR("session begin failed rc=%d off=0x%x", rc, (unsigned)detail);
        mboot_port_led(LED_ALIVE | LED_ERROR);
    }
    return rc;
}

// DFU 1.1 status for a validation result.
static uint8_t dfu_status_for(uint16_t code) {
    switch (code) {
        case MBOOT_RES_OK:
            return DFU_STATUS_OK;
        case MBOOT_RES_ERR_HASH:
        case MBOOT_RES_ERR_SIG:
        case MBOOT_RES_ERR_DOWNGRADE:
        case MBOOT_RES_ERR_NOT_TARGET:
        case MBOOT_RES_ERR_LAYOUT:
        case MBOOT_RES_ERR_TOO_SMALL:
            return DFU_STATUS_ERR_FILE;
        case MBOOT_RES_ERR_HEADER:
        case MBOOT_RES_ERR_TOO_BIG:
            return DFU_STATUS_ERR_ADDRESS;
        case MBOOT_RES_ERR_FLASH:
        case MBOOT_RES_ERR_PENDING:
            return DFU_STATUS_ERR_WRITE;
        default:
            return DFU_STATUS_ERR_UNKNOWN;
    }
}

// Erases the first sector of the view so a rejected image can't linger. With the header gone,
// neither a later boot nor a later validation accepts the remainder.
static void scramble_view(const struct flash_area *view) {
    mboot_addr_t next;
    mboot_addr_t addr = mboot_devs[view->fa_device_id].base + view->fa_off;
    if (glue_erase_sector(addr, &next) != 0) {
        MCUBOOT_LOG_ERR("cannot erase rejected image header");
    }
}

// The host finished a download: validate the image and, if it is good, mark it for
// the next boot.
uint8_t mboot_hook_manifest(uint8_t alt) {
    if (!alt_is_image(alt)) {
        return DFU_STATUS_OK;
    }
    const struct flash_area *view = mboot_dfu_recovery_target_view();
    if (view == NULL) {
        set_last_result(MBOOT_RES_ERR_LAYOUT, MBOOT_DFU_PHASE_VALIDATE, 0);
        return DFU_STATUS_ERR_TARGET;
    }

    mboot_validate_result_t r = mboot_validate_view(view,
        VALIDATE_FULL | VALIDATE_CHECK_TARGET | VALIDATE_CHECK_DOWNGRADE);
    uint8_t phase = MBOOT_DFU_PHASE_VALIDATE;

    if (r.code == MBOOT_RES_OK) {
        #if MBOOT_POLICY_SINGLE
        // Single slot: the image is already in the primary slot.
        int rc = 0;
        #else
        // Test swap: the new image reverts unless the application confirms it;
        // permanent for overwrite-external, which has no revert.
        int rc = mboot_update_mark_pending(false);
        #endif
        if (rc != 0) {
            r.code = MBOOT_RES_ERR_PENDING;
            r.detail = (uint32_t)rc;
            phase = MBOOT_DFU_PHASE_PENDING;
        }
    }

    set_last_result(r.code, phase, r.detail);

    if (r.code != MBOOT_RES_OK) {
        MCUBOOT_LOG_ERR("image rejected code=%d detail=%u", (int)r.code, (unsigned)r.detail);
        mboot_port_led(LED_ALIVE | LED_ERROR);
        if (r.code != MBOOT_RES_ERR_PENDING) {
            scramble_view(view);
        }
        return dfu_status_for(r.code);
    }
    MCUBOOT_LOG_INF("image accepted");
    mboot_port_led(LED_ALIVE | LED_USB);
    return DFU_STATUS_OK;
}

// ---------------------------------------------------------------------------
// Session loop
// ---------------------------------------------------------------------------

// Reports a table inconsistency and resets. A layout that the layout header should have
// refused leaves no safe way to run DFU.
static MBOOT_NORETURN void dfu_init_failed(int rc) {
    MCUBOOT_LOG_ERR("DFU tables inconsistent rc=%d", rc);
    mboot_port_led(LED_ALIVE | LED_ERROR);
    for (int i = 0; i < 30; i++) {
        mboot_port_wdt_feed();
        mboot_port_delay_ms(100);
    }
    mboot_port_deinit();
    mboot_port_reset();
}

// Inactivity timeout in milliseconds for a recovery cause, 0 for none. Only a recovery the
// application or the user asked for may time out. Recovery after a missing or failed image, a
// failed fsload or a fault waits for a host.
static uint32_t dfu_timeout_ms(mboot_recovery_cause_t why) {
    if ((why != REC_APP_REQUEST && why != REC_FORCED) || MBOOT_DFU_TIMEOUT_S <= 0) {
        return 0;
    }
    uint32_t s = MBOOT_DFU_TIMEOUT_S;
    if (s > 0x7FFFFFFFu / 1000u) {
        s = 0x7FFFFFFFu / 1000u;
    }
    return s * 1000u;
}

MBOOT_NORETURN void mboot_dfu_recovery_run(mboot_recovery_cause_t why) {
    int rc = mboot_dfu_recovery_regions_init();
    if (rc == 0) {
        rc = mboot_dfu_init();
    }
    if (rc != 0) {
        dfu_init_failed(rc);
    }
    mboot_usbd_init();
    if (mboot_port_usb_init() != 0 || !tusb_init()) {
        dfu_init_failed(-EIO);
    }
    mboot_port_led(LED_ALIVE);

    uint32_t timeout_ms = dfu_timeout_ms(why);
    uint32_t deadline = mboot_port_ticks_ms() + timeout_ms;
    uint32_t last_activity = mboot_usbd_activity();

    for (;;) {
        tud_task();
        mboot_port_wdt_feed();
        if (mboot_usbd_leave_requested()) {
            // Reset rather than jump: boot_go() decides what runs next.
            mboot_port_deinit();
            mboot_port_reset();
        }
        uint32_t activity = mboot_usbd_activity();
        if (activity != last_activity) {
            last_activity = activity;
            deadline = mboot_port_ticks_ms() + timeout_ms;
        }
        if (timeout_ms != 0 && (int32_t)(mboot_port_ticks_ms() - deadline) >= 0
            && !mboot_dfu_session_active()) {
            mboot_port_deinit();
            mboot_port_reset();
        }
    }
}
