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

// fake_mcuboot.c - see fake_mcuboot.h.

#include <errno.h>
#include <stdbool.h>
#include <string.h>

#include "fake_mcuboot.h"
#include "mboot_dfu.h"
#include "mboot_region.h"
#include "mcuboot_port.h"
#include "mcuboot_types.h"
#include "mcuboot_update.h"
#include "mcuboot_updatelog.h"
#include "mcuboot_dfu.h"
#include "flash_map_backend/flash_map_backend.h"
#include "sysflash/sysflash.h"
#include "tusb.h"

// ---- flash map ----

// The device and area tables are the real flash_map.c. The DFU regions and write
// ranges are the real dfu_regions.c, built with two symbols renamed so the
// tables the glue sees can be changed.

uint8_t fk_flash[FK_DEV_SIZE];

int flash_area_open(uint8_t id, const struct flash_area **fa) {
    for (unsigned i = 0; i < mcuboot_area_count; i++) {
        if (mcuboot_areas[i].fa_id == id) {
            *fa = &mcuboot_areas[i];
            return 0;
        }
    }
    return -ENOENT;
}

uint32_t flash_area_align(const struct flash_area *fa) {
    return mcuboot_devs[fa->fa_device_id].write_unit;
}

uint32_t fk_trailer_sz;

// ---- DFU region table and write ranges ----

extern const fk_wrange_t fk_real_write_ranges[];
void fk_real_get_regions(const mboot_region_t **regions_out, size_t *count_out);

const mboot_region_t *fk_regions;
size_t fk_region_count;

void mboot_port_get_regions(const mboot_region_t **regions_out, size_t *count_out) {
    *regions_out = fk_regions;
    *count_out = fk_region_count;
}

// This is the mcuboot_write_ranges[] table the glue uses.
fk_wrange_t fk_ranges[4];

static void reset_ranges(void) {
    memset(fk_ranges, 0, sizeof(fk_ranges));
    for (unsigned n = 0; fk_real_write_ranges[n].size != 0; n++) {
        fk_ranges[n] = fk_real_write_ranges[n];
    }
}

// ---- recorded events ----

fk_ev_t fk_events[FK_EV_MAX];
size_t fk_event_count;

static void ev(fk_ev_kind_t kind, uint32_t a, uint32_t b) {
    if (fk_event_count < FK_EV_MAX) {
        fk_events[fk_event_count].kind = kind;
        fk_events[fk_event_count].a = a;
        fk_events[fk_event_count].b = b;
        fk_event_count++;
    }
}

size_t fk_count(fk_ev_kind_t kind) {
    size_t n = 0;
    for (size_t i = 0; i < fk_event_count; i++) {
        if (fk_events[i].kind == kind) {
            n++;
        }
    }
    return n;
}

uint32_t fk_hash(uint32_t off, uint32_t len) {
    uint32_t h = 2166136261u;
    for (uint32_t i = 0; i < len; i++) {
        h = (h ^ fk_flash[off + i]) * 16777619u;
    }
    return h;
}

uint32_t fk_hash_protected(void) {
    #if MCUBOOT_POLICY_SINGLE
    uint32_t lo = FK_PRIMARY_OFF, hi = FK_PRIMARY_OFF + FK_PRIMARY_SIZE;
    #else
    uint32_t lo = FK_SECONDARY_OFF, hi = FK_SECONDARY_OFF + FK_SECONDARY_SIZE;
    #endif
    return fk_hash(0, lo) ^ (fk_hash(hi, FK_DEV_SIZE - hi) * 31u);
}

// ---- flash backend ----

int64_t fk_fail_erase_off;
unsigned fk_nonblank_writes;

int mcuboot_flash_dev_read(uint8_t dev, uint32_t off, void *dst, uint32_t len) {
    if (dev != 0 || off > FK_DEV_SIZE || len > FK_DEV_SIZE - off) {
        return -EINVAL;
    }
    memcpy(dst, &fk_flash[off], len);
    return 0;
}

int mcuboot_flash_dev_write(uint8_t dev, uint32_t off, const void *src, uint32_t len) {
    if (dev != 0 || off > FK_DEV_SIZE || len > FK_DEV_SIZE - off || off % FK_WRITE != 0 || len % FK_WRITE != 0) {
        return -EINVAL;
    }
    ev(EV_WRITE, off, len);
    for (uint32_t i = 0; i < len; i++) {
        if (fk_flash[off + i] != 0xFF) {
            fk_nonblank_writes++;
            break;
        }
    }
    memcpy(&fk_flash[off], src, len);
    return 0;
}

int mcuboot_flash_dev_erase(uint8_t dev, uint32_t off, uint32_t len) {
    if (dev != 0 || off > FK_DEV_SIZE || len > FK_DEV_SIZE - off || off % FK_ES != 0 || len % FK_ES != 0) {
        return -EINVAL;
    }
    ev(EV_ERASE, off, len);
    if (fk_fail_erase_off >= 0 && (uint32_t)fk_fail_erase_off >= off && (uint32_t)fk_fail_erase_off < off + len) {
        return -EIO;
    }
    memset(&fk_flash[off], 0xFF, len);
    return 0;
}

// ---- update log, validation, bootutil ----

int mcuboot_updatelog_append(uint8_t type, uint8_t result, uint8_t source,
    const mcuboot_image_info_t *info, uint32_t detail) {
    (void)source;
    (void)info;
    (void)detail;
    ev(EV_LOG, type, result);
    return 0;
}

mcuboot_validate_result_t fk_validate_result;

mcuboot_validate_result_t mcuboot_validate_view(const struct flash_area *fap, uint32_t flags) {
    ev(EV_VALIDATE, fap->fa_id, flags);
    return fk_validate_result;
}

int fk_pending_rc;

// The mcuboot_update.h update slot functions, with the same erase order as the
// real ones (trailer sectors last first, then the spare sector) but going
// through the recording flash functions. The real ones are tested by the host
// harness in tests/mcuboot/host/bootloader.
int mcuboot_update_target(mcuboot_update_target_t *t) {
    const struct flash_area *fap;
    #if MCUBOOT_POLICY_SINGLE
    int rc = flash_area_open(FLASH_AREA_IMAGE_PRIMARY(0), &fap);
    #else
    int rc = flash_area_open(FLASH_AREA_IMAGE_SECONDARY(0), &fap);
    #endif
    if (rc != 0) {
        return rc;
    }
    t->fap = fap;
    t->update_off = FK_SPARE_SIZE;
    #if MCUBOOT_POLICY_SINGLE
    // The only slot has no trailer sectors.
    t->trailer_off = fap->fa_size;
    #else
    uint32_t sectors = (fk_trailer_sz + FK_ES - 1) / FK_ES;
    t->trailer_off = fap->fa_size - sectors * FK_ES;
    #endif
    t->capacity = t->trailer_off - t->update_off;
    return 0;
}

int mcuboot_update_begin(const mcuboot_update_target_t *t, uint32_t *fail_off) {
    *fail_off = 0;
    #if MCUBOOT_POLICY_SINGLE
    // Policy single erases nothing at session begin.
    (void)t;
    #else
    for (uint32_t end = t->fap->fa_size; end > t->trailer_off; end -= FK_ES) {
        int rc = mcuboot_flash_dev_erase(0, t->fap->fa_off + end - FK_ES, FK_ES);
        if (rc != 0) {
            *fail_off = t->fap->fa_off + end - FK_ES;
            return rc;
        }
    }
    for (uint32_t off = 0; off < t->update_off; off += FK_ES) {
        int rc = mcuboot_flash_dev_erase(0, t->fap->fa_off + off, FK_ES);
        if (rc != 0) {
            *fail_off = t->fap->fa_off + off;
            return rc;
        }
    }
    #endif
    return 0;
}

int mcuboot_update_mark_pending(bool permanent) {
    #if MCUBOOT_POLICY_OVERWRITE_EXTERNAL
    permanent = true;
    #endif
    ev(EV_PENDING, 0, permanent ? 1 : 0);
    return fk_pending_rc;
}

// ---- logging ----

void mcuboot_log(int level, const char *fmt, ...) {
    (void)level;
    (void)fmt;
}

// ---- port ----

uint32_t fk_ticks;
uint32_t fk_tick_step;
unsigned fk_task_calls;
jmp_buf fk_reset_jmp;
void (*fk_task_hook)(unsigned call);
uint32_t fk_leds;

void mcuboot_port_wdt_feed(void) {
}

void mcuboot_port_led(uint32_t mask) {
    fk_leds = mask;
}

int mcuboot_port_usb_init(void) {
    return 0;
}

void mcuboot_port_get_serial_number(char *buf, size_t len) {
    if (len > 0) {
        strncpy(buf, "0123456789ABCDEF", len - 1);
        buf[len - 1] = '\0';
    }
}

uint32_t mcuboot_port_ticks_ms(void) {
    return fk_ticks;
}

void mcuboot_port_delay_ms(uint32_t ms) {
    fk_ticks += ms;
}

void mcuboot_port_deinit(void) {
    ev(EV_DEINIT, 0, 0);
}

MCUBOOT_NORETURN void mcuboot_port_reset(void) {
    ev(EV_RESET, 0, 0);
    longjmp(fk_reset_jmp, 1);
}

bool tusb_init(void) {
    return true;
}

void tud_task(void) {
    fk_ticks += fk_tick_step;
    fk_task_calls++;
    if (fk_task_hook != NULL) {
        fk_task_hook(fk_task_calls);
    }
}

// ---- setup ----

int fk_setup(void) {
    memset(fk_flash, 0xFF, sizeof(fk_flash));
    fk_event_count = 0;
    fk_nonblank_writes = 0;
    fk_fail_erase_off = -1;
    fk_trailer_sz = MCUBOOT_TRAILER_SIZE;
    memset(&fk_validate_result, 0, sizeof(fk_validate_result));
    fk_pending_rc = 0;
    fk_ticks = 1000;
    fk_tick_step = 0;
    fk_task_calls = 0;
    fk_task_hook = NULL;
    fk_leds = 0;
    fk_real_get_regions(&fk_regions, &fk_region_count);
    reset_ranges();
    int rc = mcuboot_dfu_regions_init();
    mboot_dfu_init();
    return rc;
}
