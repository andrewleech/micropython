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

#define _GNU_SOURCE
#include <errno.h>
#include <inttypes.h>
#include <limits.h>
#include <stdarg.h>
#include <stdio.h>
#include <string.h>
#include <sys/wait.h>
#include <unistd.h>

#include "mcuboot_config/mcuboot_config.h"
#include "bootutil/security_cnt.h"
#include "flash_map_backend/flash_map_backend.h"
#include "sysflash/sysflash.h"
#include "mboot_crc32.h"
#include "mboot_log.h"
#include "mboot_request.h"
#include "mboot_seccnt.h"
#include "mboot_updatelog.h"
#include "fake_flash.h"
#include "core.h"

int core_failures;

bool core_check(bool cond, const char *expr, const char *file, int line) {
    if (!cond) {
        core_failures++;
        printf("  CHECK FAILED %s:%d: %s\n", file, line, expr);
    }
    return cond;
}

bool core_force_ecc;

// A fresh fake flash described by the generated device table. Device 1, when the layout has one,
// is plain memory: the core tests do not use its content.
void core_flash_fresh(void) {
    fake_flash_dev_cfg_t cfg[FAKE_FLASH_MAX_DEVS];
    memset(cfg, 0, sizeof(cfg));
    for (unsigned i = 0; i < mboot_dev_count; i++) {
        const mboot_flash_dev_t *d = &mboot_devs[i];
        cfg[i].size = d->size;
        cfg[i].n_runs = d->run_count;
        for (unsigned j = 0; j < d->run_count; j++) {
            cfg[i].runs[j].size = d->runs[j].size;
            cfg[i].runs[j].erase = d->runs[j].erase;
        }
        cfg[i].write_unit = d->write_unit;
        cfg[i].erased_val = d->erased_val;
        if (i == 0) {
            #if defined(CORE_MAP_FLASH)
            cfg[i].map_base = d->base;    // place the memory at the device base address for builds that hash in place
            #else
            cfg[i].map_base = d->mapped ? d->base : 0;
            #endif
            #if defined(MBOOT_ECC_SHADOW)
            cfg[i].ecc = true;
            #else
            cfg[i].ecc = core_force_ecc;
            // Without the ECC policy the glue relies on what bootutil relies on: a write unit is
            // programmed or not.
            cfg[i].atomic_program = !core_force_ecc;
            #endif
        } else {
            cfg[i].atomic_program = true;
        }
    }
    fake_flash_init(mboot_dev_count, cfg);
}

static const struct flash_area *area(uint8_t id) {
    const struct flash_area *fa = NULL;
    if (flash_area_open(id, &fa) != 0) {
        return NULL;
    }
    return fa;
}

// ---- printf_lite and crc32 ----

static int local_snprintf(char *buf, size_t size, const char *format, ...) {
    va_list ap;
    va_start(ap, format);
    int n = mboot_vsnprintf(buf, size, format, ap);
    va_end(ap);
    return n;
}

static void check_fmt(const char *name, const char *got, const char *want) {
    if (strcmp(got, want) != 0) {
        core_failures++;
        printf("  CHECK FAILED printf %s: got '%s' want '%s'\n", name, got, want);
    }
}

#define FMT_CASE(fmt, ...) \
    do { \
        char a[64], b[64]; \
        int na = local_snprintf(a, sizeof(a), fmt, __VA_ARGS__); \
        int nb = snprintf(b, sizeof(b), fmt, __VA_ARGS__); \
        check_fmt(fmt, a, b); \
        CORE_CHECK(na == nb); \
    } while (0)

static void test_printf(void) {
    printf("printf_lite\n");
    FMT_CASE("%d", 0);
    FMT_CASE("%d", -5);
    FMT_CASE("%d", INT_MIN);
    FMT_CASE("%i", 123456);
    FMT_CASE("%u", 4000000000u);
    FMT_CASE("%x", 0xDEADBEEFu);
    FMT_CASE("%X", 0xDEADBEEFu);
    FMT_CASE("%08x", 0x1234u);
    FMT_CASE("%02x%02x", 1, 0xAB);
    FMT_CASE("%5d|", 42);
    FMT_CASE("%-5d|", 42);
    FMT_CASE("%05d", -42);
    FMT_CASE("%lu", ULONG_MAX);
    FMT_CASE("%lx", 0xCAFEBABEUL);
    FMT_CASE("%ld", LONG_MIN);
    FMT_CASE("%zu", (size_t)77);
    FMT_CASE("%c", 'x');
    FMT_CASE("%3c|", 'x');
    FMT_CASE("%-3c|", 'x');
    FMT_CASE("%s", "text");
    FMT_CASE("%8s|", "text");
    FMT_CASE("%-8s|", "text");
    FMT_CASE("%.2s|", "text");
    const char *volatile null_str = NULL;
    FMT_CASE("%s", null_str);
    FMT_CASE("100%%%s", "!");
    FMT_CASE("%" PRIu32 " %" PRIx32, (uint32_t)7, (uint32_t)0xBEEF);
    FMT_CASE("%s: image %d at %08lx", "boot_go", 3, 0x8000UL);

    char buf[64];
    char small[8];
    int n = local_snprintf(small, sizeof(small), "%s", "0123456789");
    CORE_CHECK(n == 10);
    CORE_CHECK(strcmp(small, "0123456") == 0);
    n = local_snprintf(small, 0, "%d", 12345);
    CORE_CHECK(n == 5);
    int dummy;
    n = local_snprintf(buf, sizeof(buf), "%p", (void *)&dummy);
    char want[40];
    snprintf(want, sizeof(want), "0x%" PRIxPTR, (uintptr_t)&dummy);
    CORE_CHECK(n > 2 && strcmp(buf, want) == 0);
    local_snprintf(buf, sizeof(buf), "a%qb", 1);
    CORE_CHECK(strcmp(buf, "a%qb") == 0);
}

static void test_crc32(void) {
    printf("crc32\n");
    CORE_CHECK(mboot_crc32(0, "123456789", 9) == 0xCBF43926u);
    CORE_CHECK(mboot_crc32(0, "", 0) == 0);
    uint32_t c = mboot_crc32(0, "1234", 4);
    c = mboot_crc32(c, "56789", 5);
    CORE_CHECK(c == 0xCBF43926u);
    uint8_t z[32] = {0};
    CORE_CHECK(mboot_crc32(0, z, 32) == 0x190A55ADu);
}

// ---- flash_map_backend ----

static void test_flash_map(void) {
    printf("flash_map_backend\n");
    core_flash_fresh();
    CORE_CHECK(mboot_flash_map_check() == 0);

    const struct flash_area *pri = area(FLASH_AREA_IMAGE_PRIMARY(0));
    const struct flash_area *sec = area(FLASH_AREA_IMAGE_SECONDARY(0));
    CORE_CHECK(pri != NULL && sec != NULL);
    const struct flash_area *none;
    CORE_CHECK(flash_area_open(MBOOT_AREA_ID_VIEW, &none) == -ENOENT);
    CORE_CHECK(flash_area_id_from_image_slot(0) == FLASH_AREA_IMAGE_PRIMARY(0));
    CORE_CHECK(flash_area_id_from_multi_image_slot(0, 1) == FLASH_AREA_IMAGE_SECONDARY(0));
    CORE_CHECK(flash_area_id_from_multi_image_slot(1, 0) == -EINVAL);
    CORE_CHECK(flash_area_id_from_multi_image_slot(0, 2) == -EINVAL);
    if (pri == NULL || sec == NULL) {
        return;
    }

    const mboot_flash_dev_t *d = &mboot_devs[pri->fa_device_id];
    uint32_t eu = mboot_area_erase(pri);
    CORE_CHECK(eu != 0 && eu == MBOOT_SLOT_UNIT && mboot_area_erase(sec) == eu);
    CORE_CHECK(flash_area_align(pri) == d->write_unit);
    CORE_CHECK(flash_area_erased_val(pri) == d->erased_val);
    uintptr_t base = 0;
    CORE_CHECK(flash_device_base(0, &base) == 0 && base == d->base);
    CORE_CHECK(flash_device_base(7, &base) == -ENODEV);

    // Bounds are checked against the area, not only the device.
    uint8_t buf[64];
    CORE_CHECK(flash_area_read(pri, 0, buf, 16) == 0);
    CORE_CHECK(flash_area_read(pri, pri->fa_size - 16, buf, 16) == 0);
    CORE_CHECK(flash_area_read(pri, pri->fa_size - 8, buf, 16) == -EINVAL);
    CORE_CHECK(flash_area_read(pri, pri->fa_size + 1, buf, 0) == -EINVAL);
    CORE_CHECK(flash_area_read(pri, UINT32_MAX - 4, buf, 16) == -EINVAL);
    CORE_CHECK(flash_area_write(pri, pri->fa_size - 8, buf, 16) == -EINVAL);
    CORE_CHECK(flash_area_erase(pri, pri->fa_size, eu) == -EINVAL);
    // Alignment is enforced.
    CORE_CHECK(flash_area_write(pri, 1, buf, d->write_unit) == -EINVAL);
    CORE_CHECK(flash_area_write(pri, 0, buf, d->write_unit + 1) == -EINVAL);
    CORE_CHECK(flash_area_erase(pri, 4, eu) == -EINVAL);
    CORE_CHECK(flash_area_erase(pri, 0, eu + d->write_unit) == -EINVAL);
    CORE_CHECK(fake_flash_stats()->violations == 0);

    // Sectors.
    struct flash_sector fs;
    CORE_CHECK(flash_area_get_sector(pri, 0, &fs) == 0 && fs.fs_off == 0 && fs.fs_size == eu);
    CORE_CHECK(flash_area_get_sector(pri, eu + 5, &fs) == 0 && fs.fs_off == eu);
    CORE_CHECK(flash_area_get_sector(pri, pri->fa_size - 1, &fs) == 0 && fs.fs_off == pri->fa_size - eu);
    CORE_CHECK(flash_area_get_sector(pri, pri->fa_size, &fs) == -ERANGE);
    CORE_CHECK(flash_area_get_sector(pri, -1, &fs) == -ERANGE);

    uint32_t want = sec->fa_size / eu;
    static struct flash_sector sectors[MCUBOOT_MAX_IMG_SECTORS + 8];
    uint32_t count = MCUBOOT_MAX_IMG_SECTORS + 8;
    CORE_CHECK(flash_area_get_sectors(FLASH_AREA_IMAGE_SECONDARY(0), &count, sectors) == 0);
    CORE_CHECK(count == want);
    CORE_CHECK(sectors[count - 1].fs_off == sec->fa_size - eu && sectors[count - 1].fs_size == eu);
    count = want - 1;
    CORE_CHECK(flash_area_get_sectors(FLASH_AREA_IMAGE_SECONDARY(0), &count, sectors) == -ENOMEM);
    count = 4;
    CORE_CHECK(flash_area_get_sectors(200, &count, sectors) == -ENOENT);

    // Write then read back through the area; an erase makes the data erased again.
    for (int i = 0; i < 64; i++) {
        buf[i] = (uint8_t)(i * 7 + 1);
    }
    uint8_t back[64];
    uint32_t woff = 2 * eu;
    CORE_CHECK(flash_area_write(sec, woff, buf, 64) == 0);
    CORE_CHECK(flash_area_read(sec, woff, back, 64) == 0 && memcmp(buf, back, 64) == 0);
    CORE_CHECK(flash_area_erase(sec, woff, eu) == 0);
    CORE_CHECK(flash_area_read(sec, woff, back, 64) == 0 && back[0] == d->erased_val && back[63] == d->erased_val);
    CORE_CHECK(fake_flash_stats()->overprograms == 0);

    // The synthetic stream device is read only and has no sectors.
    struct flash_area stream = {.fa_id = MBOOT_AREA_ID_STREAM, .fa_device_id = MBOOT_DEV_STREAM, .fa_off = 0, .fa_size = 4096};
    CORE_CHECK(flash_area_write(&stream, 0, buf, 16) == -EACCES);
    CORE_CHECK(flash_area_erase(&stream, 0, 4096) == -EACCES);
    CORE_CHECK(flash_area_get_sector(&stream, 0, &fs) != 0);
    CORE_CHECK(flash_area_align(&stream) == 1);

    // A view (copy of the secondary area shifted by one sector) reads and writes through the
    // same device offsets.
    struct flash_area view = *sec;
    view.fa_id = MBOOT_AREA_ID_VIEW;
    view.fa_off += eu;
    view.fa_size -= eu;
    CORE_CHECK(flash_area_write(&view, eu, buf, 64) == 0);
    CORE_CHECK(flash_area_read(sec, 2 * eu, back, 64) == 0 && memcmp(buf, back, 64) == 0);
    core_flash_fresh();
}

// The areas that the slot policy and the swap mode add or remove.
static void test_policy_areas(void) {
    printf("areas of the slot policy\n");
    core_flash_fresh();
    const struct flash_area *pri = area(FLASH_AREA_IMAGE_PRIMARY(0));
    uint32_t unit = mboot_area_erase(pri);
    (void)unit;
    const struct flash_area *scratch = area(FLASH_AREA_IMAGE_SCRATCH);
    const struct flash_area *intent = area(MBOOT_AREA_INTENT);
    #if MBOOT_POLICY_SINGLE
    // One slot: both image slots of bootutil are the primary slot.
    CORE_CHECK(area(FLASH_AREA_IMAGE_SECONDARY(0)) == pri);
    #else
    CORE_CHECK(area(FLASH_AREA_IMAGE_SECONDARY(0)) != NULL && area(FLASH_AREA_IMAGE_SECONDARY(0)) != pri);
    #endif
    #if defined(MCUBOOT_SWAP_USING_SCRATCH)
    CORE_CHECK(scratch != NULL && scratch->fa_size >= unit && scratch->fa_size % unit == 0);
    #else
    CORE_CHECK(scratch == NULL);
    #endif
    #if defined(MBOOT_INTENT_ADDR)
    CORE_CHECK(intent != NULL && intent->fa_size == mboot_area_erase(intent));
    #else
    CORE_CHECK(intent == NULL);
    #endif
    #if defined(MCUBOOT_SWAP_USING_MOVE)
    const struct flash_area *sec = area(FLASH_AREA_IMAGE_SECONDARY(0));
    CORE_CHECK(pri->fa_size == sec->fa_size + unit);
    #endif
    // The trailer sectors of the layout are where the slot trailer is.
    uint32_t trailer_off;
    CORE_CHECK(mboot_flash_slot_trailer_off(pri, &trailer_off) == 0);
    #if MBOOT_POLICY_SINGLE
    // The only slot holds no swap state: it has no trailer sectors.
    CORE_CHECK(trailer_off == pri->fa_size);
    #else
    CORE_CHECK(trailer_off == pri->fa_size - MBOOT_TRAILER_SECTORS * unit);
    #endif
}

// The recovery primitive: trailer sectors of a slot are erased and nothing else.
static void test_erase_trailer(void) {
    printf("erase trailer\n");
    core_flash_fresh();
    const struct flash_area *pri = area(FLASH_AREA_IMAGE_PRIMARY(0));
    uint32_t unit = mboot_area_erase(pri);
    (void)unit;
    uint32_t wu = mboot_devs[0].write_unit;
    uint8_t buf[32];
    uint8_t rd[32];
    memset(buf, 0x5A, sizeof(buf));
    #if MBOOT_POLICY_SINGLE
    // The only slot has no trailer sectors: nothing is erased.
    CORE_CHECK(flash_area_write(pri, pri->fa_size - wu, buf, wu) == 0);
    CORE_CHECK(mboot_flash_erase_trailer(FLASH_AREA_IMAGE_PRIMARY(0)) == 0);
    CORE_CHECK(flash_area_read(pri, pri->fa_size - wu, rd, wu) == 0 && rd[0] == 0x5A);
    #else
    const struct flash_area *sec = area(FLASH_AREA_IMAGE_SECONDARY(0));
    uint32_t sectors = MBOOT_TRAILER_SECTORS;
    // body word below the trailer, then words in the last sector and the first sector of the trailer
    CORE_CHECK(flash_area_write(pri, pri->fa_size - sectors * unit - wu, buf, wu) == 0);
    CORE_CHECK(flash_area_write(pri, pri->fa_size - wu, buf, wu) == 0);
    CORE_CHECK(flash_area_write(pri, pri->fa_size - sectors * unit, buf, wu) == 0);
    CORE_CHECK(flash_area_write(sec, sec->fa_size - wu, buf, wu) == 0);

    CORE_CHECK(mboot_flash_erase_trailer(FLASH_AREA_IMAGE_PRIMARY(0)) == 0);
    CORE_CHECK(flash_area_read(pri, pri->fa_size - wu, rd, wu) == 0 && rd[0] == mboot_devs[0].erased_val);
    CORE_CHECK(flash_area_read(pri, pri->fa_size - sectors * unit, rd, wu) == 0 && rd[0] == mboot_devs[0].erased_val);
    CORE_CHECK(flash_area_read(pri, pri->fa_size - sectors * unit - wu, rd, wu) == 0 && rd[0] == 0x5A);
    CORE_CHECK(flash_area_read(sec, sec->fa_size - wu, rd, wu) == 0 && rd[0] == 0x5A);
    CORE_CHECK(mboot_flash_erase_trailer(MBOOT_AREA_ID_VIEW) == -ENOENT);
    CORE_CHECK(mboot_flash_erase_trailer(FLASH_AREA_IMAGE_SECONDARY(0)) == 0);
    CORE_CHECK(flash_area_read(sec, sec->fa_size - wu, rd, wu) == 0 && rd[0] == mboot_devs[0].erased_val);
    #endif
    CORE_CHECK(fake_flash_stats()->violations == 0 && fake_flash_stats()->overprograms == 0);
    #if defined(MBOOT_ECC_SHADOW) && !MBOOT_POLICY_SINGLE
    // the shadow sectors are gone with the trailer sectors: a scrub has nothing to do
    fake_flash_counter_reset();
    CORE_CHECK(mboot_flash_scrub() == 0);
    CORE_CHECK(fake_flash_stats()->writes == 0 && fake_flash_stats()->erases == 0);
    #endif
    core_flash_fresh();
}

// ---- ECC shadow words ----

#if defined(MBOOT_ECC_SHADOW)

typedef struct {
    const struct flash_area *pri, *sec, *shadow;
    uint32_t eu, wu, nsec;
    uint32_t prot[2];
} geom_t;

static void geom_get(geom_t *g) {
    g->pri = area(FLASH_AREA_IMAGE_PRIMARY(0));
    g->sec = area(FLASH_AREA_IMAGE_SECONDARY(0));
    g->shadow = area(MBOOT_AREA_SHADOW);
    g->eu = mboot_area_erase(g->pri);
    g->wu = mboot_devs[0].write_unit;
    g->nsec = g->shadow->fa_size / (2 * g->eu);
    g->prot[0] = g->pri->fa_size - g->nsec * g->eu;
    g->prot[1] = g->sec->fa_size - g->nsec * g->eu;
}

// Area offset of the shadow of the word at off in the trailer sector of slot s.
static uint32_t shadow_of(const geom_t *g, int s, uint32_t off) {
    return s * g->nsec * g->eu + (off - g->prot[s]);
}

// ST_WEAK_ERASED and ST_WEAK_DATA are words that a cut left partly programmed and that read
// "corrected" without a double error: with the erased value, or with a partial program of A.
typedef enum { ST_ERASED, ST_A, ST_B, ST_INVALID, ST_WEAK_ERASED, ST_WEAK_DATA } ustate_t;

static void unit_fill(uint8_t *u, uint32_t wu, uint8_t v) {
    for (uint32_t i = 0; i < wu; i++) {
        u[i] = (uint8_t)(v + i);
    }
}

// Puts a device word into a state: erased, valid with pattern A or B, or ECC invalid (a
// second program with different data, as measured on the H5).
static void unit_set(uint32_t dev_off, ustate_t st) {
    uint8_t u[32];
    uint32_t wu = mboot_devs[0].write_unit;
    fake_flash_erase_raw(0, dev_off, wu);
    switch (st) {
        case ST_ERASED:
            break;
        case ST_A:
            unit_fill(u, wu, 0x10);
            fake_flash_poke(0, dev_off, u, wu);
            break;
        case ST_B:
            unit_fill(u, wu, 0x50);
            fake_flash_poke(0, dev_off, u, wu);
            break;
        case ST_INVALID:
            unit_fill(u, wu, 0x10);
            fake_flash_poke(0, dev_off, u, wu);
            unit_fill(u, wu, 0x90);
            mboot_port_flash_dev_write(0, dev_off, u, wu);
            break;
        case ST_WEAK_ERASED:
            fake_flash_set_weak(0, dev_off, NULL);
            break;
        case ST_WEAK_DATA:
            unit_fill(u, wu, 0x10);
            for (uint32_t i = 0; i < wu; i++) {
                u[i] |= 0xA5;
            }
            fake_flash_set_weak(0, dev_off, u);
            break;
    }
}

static const char *ustate_name(ustate_t s) {
    static const char *n[] = {"erased", "A", "B", "invalid", "corrected erased", "corrected data"};
    return n[s];
}

// Every combination of a primary word and its shadow word reads as the read rule says.
static void test_shadow_read_rule(void) {
    printf("shadow read rule\n");
    core_flash_fresh();
    geom_t g;
    geom_get(&g);
    const uint32_t off = g.prot[0] + 3 * g.wu;
    const uint32_t poff = g.pri->fa_off + off;
    const uint32_t soff = g.shadow->fa_off + shadow_of(&g, 0, off);
    uint8_t a[32], b[32], got[32], erased[32];
    unit_fill(a, g.wu, 0x10);
    unit_fill(b, g.wu, 0x50);
    memset(erased, mboot_devs[0].erased_val, sizeof(erased));

    for (ustate_t p = ST_ERASED; p <= ST_WEAK_DATA; p++) {
        if (p == ST_B) {
            continue;
        }
        for (ustate_t s = ST_ERASED; s <= ST_WEAK_DATA; s++) {
            unit_set(poff, p);
            unit_set(soff, s);
            // A shadow word that is invalid or corrected counts as missing.
            const uint8_t *want;
            if (p == ST_A) {
                want = a;
            } else if (p == ST_ERASED) {
                want = erased;
            } else {
                want = (s == ST_A) ? a : (s == ST_B) ? b : erased;
            }
            memset(got, 0x77, sizeof(got));
            int rc = flash_area_read(g.pri, off, got, g.wu);
            if (rc != 0 || memcmp(got, want, g.wu) != 0) {
                core_failures++;
                printf("  CHECK FAILED read P=%s S=%s rc=%d\n", ustate_name(p), ustate_name(s), rc);
            }
        }
    }

    // Reads that span a few words with a mix of states and unaligned start/end.
    core_flash_fresh();
    uint32_t base = g.prot[1];
    unit_set(g.sec->fa_off + base + 0 * g.wu, ST_A);
    unit_set(g.sec->fa_off + base + 1 * g.wu, ST_INVALID);
    unit_set(g.shadow->fa_off + shadow_of(&g, 1, base + 1 * g.wu), ST_B);
    unit_set(g.sec->fa_off + base + 2 * g.wu, ST_INVALID);        // shadow stays erased
    unit_set(g.sec->fa_off + base + 3 * g.wu, ST_ERASED);
    unit_set(g.sec->fa_off + base + 4 * g.wu, ST_INVALID);
    unit_set(g.shadow->fa_off + shadow_of(&g, 1, base + 4 * g.wu), ST_INVALID);
    unit_set(g.sec->fa_off + base + 5 * g.wu, ST_A);
    uint8_t span[6 * 32];
    uint8_t want[6 * 32];
    unit_fill(want + 0 * g.wu, g.wu, 0x10);
    unit_fill(want + 1 * g.wu, g.wu, 0x50);
    memset(want + 2 * g.wu, mboot_devs[0].erased_val, 3 * g.wu);
    unit_fill(want + 5 * g.wu, g.wu, 0x10);
    CORE_CHECK(flash_area_read(g.sec, base, span, 6 * g.wu) == 0);
    CORE_CHECK(memcmp(span, want, 6 * g.wu) == 0);
    // unaligned window across words 1..2
    CORE_CHECK(flash_area_read(g.sec, base + g.wu + 3, span, g.wu + 2) == 0);
    CORE_CHECK(memcmp(span, want + g.wu + 3, g.wu + 2) == 0);

    // Outside the protected sectors an invalid word is an error for the caller.
    core_flash_fresh();
    unit_set(g.pri->fa_off + 5 * g.wu, ST_INVALID);
    CORE_CHECK(flash_area_read(g.pri, 5 * g.wu, got, g.wu) == -EIO);
    CORE_CHECK(flash_area_read(g.pri, 4 * g.wu, got, g.wu) == 0);
    CORE_CHECK(flash_area_read(g.pri, 4 * g.wu, span, 3 * g.wu) == -EIO);

    // A corrected word outside the protected sectors is data the controller has corrected: it is
    // returned as it is.
    core_flash_fresh();
    unit_set(g.pri->fa_off + 5 * g.wu, ST_WEAK_DATA);
    unit_fill(want, g.wu, 0x10);
    for (uint32_t i = 0; i < g.wu; i++) {
        want[i] |= 0xA5;
    }
    memset(got, 0x77, sizeof(got));
    CORE_CHECK(flash_area_read(g.pri, 5 * g.wu, got, g.wu) == 0 && memcmp(got, want, g.wu) == 0);
    CORE_CHECK(flash_area_read(g.pri, 4 * g.wu, span, 3 * g.wu) == 0 && memcmp(span + g.wu, want, g.wu) == 0);
}

// An invalid word in the image header window of a slot (the first 32 bytes of its first and second
// sector) reads as erased, so that bootutil can read the headers of a slot whose first sector was
// being copied when the power failed. Any other word of the body stays an error.
static void test_shadow_header_window(void) {
    printf("header window\n");
    core_flash_fresh();
    geom_t g;
    geom_get(&g);
    uint8_t got[64];
    const struct flash_area *slots[2] = {g.pri, g.sec};
    for (int s = 0; s < 2; s++) {
        for (uint32_t sector = 0; sector < 2; sector++) {
            uint32_t base = sector * g.eu;
            for (uint32_t w = 0; w < 2; w++) {
                core_flash_fresh();
                unit_set(slots[s]->fa_off + base + w * g.wu, ST_INVALID);
                memset(got, 0x77, sizeof(got));
                CORE_CHECK(flash_area_read(slots[s], base, got, 2 * g.wu) == 0);
                for (uint32_t i = 0; i < 2 * g.wu; i++) {
                    CORE_CHECK(got[i] == mboot_devs[0].erased_val || i / g.wu != w);
                }
                CORE_CHECK(flash_area_read(slots[s], base, got, 32) == 0);
            }
            core_flash_fresh();
            unit_set(slots[s]->fa_off + base + 2 * g.wu, ST_INVALID);
            CORE_CHECK(flash_area_read(slots[s], base + 2 * g.wu, got, g.wu) == -EIO);
        }
        core_flash_fresh();
        unit_set(slots[s]->fa_off + 2 * g.eu, ST_INVALID);
        CORE_CHECK(flash_area_read(slots[s], 2 * g.eu, got, g.wu) == -EIO);
    }
}

// Writes to a word with a shadow follow the write rule for every state of the pair.
static void test_shadow_write_rule(void) {
    printf("shadow write rule\n");
    core_flash_fresh();
    geom_t g;
    geom_get(&g);
    const uint32_t off = g.prot[0] + 7 * g.wu;
    const uint32_t poff = g.pri->fa_off + off;
    const uint32_t soff = g.shadow->fa_off + shadow_of(&g, 0, off);
    uint8_t a[32], b[32], got[32], dev[32];
    unit_fill(a, g.wu, 0x10);
    unit_fill(b, g.wu, 0x50);

    // Both erased: both programmed, no overprogram.
    CORE_CHECK(flash_area_write(g.pri, off, a, g.wu) == 0);
    fake_flash_peek(0, poff, dev, g.wu);
    CORE_CHECK(memcmp(dev, a, g.wu) == 0);
    fake_flash_peek(0, soff, dev, g.wu);
    CORE_CHECK(memcmp(dev, a, g.wu) == 0);
    CORE_CHECK(fake_flash_stats()->overprograms == 0);

    // Primary invalid, shadow erased: the primary word is left alone, the shadow gets the value.
    unit_set(poff, ST_INVALID);
    unit_set(soff, ST_ERASED);
    uint8_t before[32];
    fake_flash_peek(0, poff, before, g.wu);
    CORE_CHECK(flash_area_write(g.pri, off, b, g.wu) == 0);
    fake_flash_peek(0, poff, dev, g.wu);
    CORE_CHECK(memcmp(dev, before, g.wu) == 0 && fake_flash_unit_ecc_invalid(0, poff));
    fake_flash_peek(0, soff, dev, g.wu);
    CORE_CHECK(memcmp(dev, b, g.wu) == 0);
    CORE_CHECK(flash_area_read(g.pri, off, got, g.wu) == 0 && memcmp(got, b, g.wu) == 0);
    CORE_CHECK(fake_flash_stats()->overprograms == 1);    // the unit_set() that made the word invalid

    // Primary written, shadow erased (the shadow write was cut): a rewrite completes the shadow.
    core_flash_fresh();
    fake_flash_poke(0, poff, a, g.wu);
    CORE_CHECK(flash_area_write(g.pri, off, a, g.wu) == 0);
    fake_flash_peek(0, soff, dev, g.wu);
    CORE_CHECK(memcmp(dev, a, g.wu) == 0 && fake_flash_stats()->overprograms == 0);

    // Primary written: other data is ignored and never programs over the word.
    CORE_CHECK(flash_area_write(g.pri, off, b, g.wu) == 0);
    fake_flash_peek(0, poff, dev, g.wu);
    CORE_CHECK(memcmp(dev, a, g.wu) == 0 && fake_flash_stats()->overprograms == 0);

    // A write that straddles the body and the first protected word splits correctly.
    core_flash_fresh();
    uint8_t span[4 * 32];
    for (uint32_t i = 0; i < sizeof(span); i++) {
        span[i] = (uint8_t)(i + 1);
    }
    uint32_t start = g.prot[1] - 2 * g.wu;
    CORE_CHECK(flash_area_write(g.sec, start, span, 4 * g.wu) == 0);
    uint8_t rd[4 * 32];
    fake_flash_peek(0, g.sec->fa_off + start, rd, 4 * g.wu);
    CORE_CHECK(memcmp(rd, span, 4 * g.wu) == 0);
    fake_flash_peek(0, g.shadow->fa_off + shadow_of(&g, 1, g.prot[1]), rd, 2 * g.wu);
    CORE_CHECK(memcmp(rd, span + 2 * g.wu, 2 * g.wu) == 0);
    // The two body words have no shadow.
    uint32_t cnt = 0;
    for (uint32_t o = 0; o < g.shadow->fa_size; o += g.wu) {
        uint8_t w[32];
        fake_flash_peek(0, g.shadow->fa_off + o, w, g.wu);
        for (uint32_t i = 0; i < g.wu; i++) {
            if (w[i] != mboot_devs[0].erased_val) {
                cnt++;
                break;
            }
        }
    }
    CORE_CHECK(cnt == 2);
    CORE_CHECK(fake_flash_stats()->overprograms == 0);
}

// The primary sector is erased before its shadow, and the scrub repairs both cut states.
static void test_shadow_erase_scrub(void) {
    printf("shadow erase order and scrub\n");
    core_flash_fresh();
    geom_t g;
    geom_get(&g);
    uint8_t a[32], dev[32], got[32];
    unit_fill(a, g.wu, 0x10);
    const uint32_t off = g.prot[0] + g.wu;
    const uint32_t poff = g.pri->fa_off + off;
    const uint32_t soff = g.shadow->fa_off + shadow_of(&g, 0, off);
    const uint32_t psec = g.pri->fa_off + g.prot[0];

    CORE_CHECK(flash_area_write(g.pri, off, a, g.wu) == 0);
    fake_flash_counter_reset();
    CORE_CHECK(flash_area_erase(g.pri, g.prot[0], g.eu) == 0);
    const fake_flash_op_t *tr = fake_flash_trace();
    CORE_CHECK(fake_flash_trace_count() == 2);
    CORE_CHECK(tr[0].kind == FAKE_FLASH_OP_ERASE && tr[0].off == psec);
    CORE_CHECK(tr[1].kind == FAKE_FLASH_OP_ERASE && tr[1].off == g.shadow->fa_off + shadow_of(&g, 0, g.prot[0]));

    // Cut between the two erases: the word reads as erased although the shadow still holds a value.
    core_flash_fresh();
    CORE_CHECK(flash_area_write(g.pri, off, a, g.wu) == 0);
    fake_flash_peek(0, poff, dev, g.wu);
    fake_flash_erase_raw(0, psec, g.eu);
    fake_flash_peek(0, soff, dev, g.wu);
    CORE_CHECK(memcmp(dev, a, g.wu) == 0);
    memset(got, 0x77, sizeof(got));
    CORE_CHECK(flash_area_read(g.pri, off, got, g.wu) == 0 && got[0] == mboot_devs[0].erased_val);
    CORE_CHECK(mboot_flash_scrub() == 0);
    fake_flash_peek(0, soff, dev, g.wu);
    CORE_CHECK(dev[0] == mboot_devs[0].erased_val);

    // An erase of the primary sector that stopped half way leaves old words next to erased
    // ones; the scrub finishes it, primary sector first, so the sector reads as erased.
    core_flash_fresh();
    uint32_t last = g.eu - g.wu;
    CORE_CHECK(flash_area_write(g.pri, g.prot[0] + g.wu, a, g.wu) == 0);
    CORE_CHECK(flash_area_write(g.pri, g.prot[0] + 100 * g.wu, a, g.wu) == 0);
    CORE_CHECK(flash_area_write(g.pri, g.prot[0] + last, a, g.wu) == 0);
    fake_flash_erase_raw(0, psec, g.eu / 2);
    CORE_CHECK(flash_area_read(g.pri, g.prot[0] + last, got, g.wu) == 0 && memcmp(got, a, g.wu) == 0);
    fake_flash_counter_reset();
    CORE_CHECK(mboot_flash_scrub() == 0);
    CORE_CHECK(fake_flash_trace_count() == 2 && fake_flash_trace()[0].off == psec);
    CORE_CHECK(flash_area_read(g.pri, g.prot[0] + last, got, g.wu) == 0 && got[0] == mboot_devs[0].erased_val);
    fake_flash_peek(0, g.shadow->fa_off + shadow_of(&g, 0, g.prot[0] + last), dev, g.wu);
    CORE_CHECK(dev[0] == mboot_devs[0].erased_val);

    // Primary word written, shadow missing: the scrub completes it and leaves the primary alone.
    core_flash_fresh();
    fake_flash_poke(0, poff, a, g.wu);
    CORE_CHECK(mboot_flash_scrub() == 0);
    fake_flash_peek(0, soff, dev, g.wu);
    CORE_CHECK(memcmp(dev, a, g.wu) == 0);
    fake_flash_peek(0, poff, dev, g.wu);
    CORE_CHECK(memcmp(dev, a, g.wu) == 0 && fake_flash_stats()->overprograms == 0);

    // A shadow word that reads corrected (a cut at the start of its program) cannot take a
    // program: the scrub leaves it alone and the primary word stays readable.
    core_flash_fresh();
    fake_flash_poke(0, poff, a, g.wu);
    unit_set(soff, ST_WEAK_ERASED);
    fake_flash_counter_reset();
    CORE_CHECK(mboot_flash_scrub() == 0);
    CORE_CHECK(fake_flash_stats()->writes == 0 && fake_flash_stats()->erases == 0);
    CORE_CHECK(flash_area_read(g.pri, off, got, g.wu) == 0 && memcmp(got, a, g.wu) == 0);

    // Primary word that reads corrected, shadow erased: reads erased (the write was cut), a
    // later write of the flag goes to the shadow only.
    core_flash_fresh();
    unit_set(poff, ST_WEAK_DATA);
    CORE_CHECK(mboot_flash_scrub() == 0);
    CORE_CHECK(fake_flash_stats()->writes == 0);
    CORE_CHECK(flash_area_read(g.pri, off, got, g.wu) == 0 && got[0] == mboot_devs[0].erased_val);
    uint8_t b[32];
    unit_fill(b, g.wu, 0x50);
    CORE_CHECK(flash_area_write(g.pri, off, b, g.wu) == 0);
    CORE_CHECK(flash_area_read(g.pri, off, got, g.wu) == 0 && memcmp(got, b, g.wu) == 0);
    CORE_CHECK(fake_flash_stats()->overprograms == 0);
    CORE_CHECK(mboot_flash_scrub() == 0);

    // Primary invalid with a valid shadow stays as it is.
    unit_set(poff, ST_INVALID);
    unit_set(soff, ST_B);
    fake_flash_counter_reset();
    CORE_CHECK(mboot_flash_scrub() == 0);
    CORE_CHECK(fake_flash_stats()->writes == 0 && fake_flash_stats()->erases == 0);
    CORE_CHECK(flash_area_read(g.pri, off, got, g.wu) == 0 && got[0] == 0x50);

    // The scrub of a clean device does no flash writes.
    core_flash_fresh();
    fake_flash_counter_reset();
    CORE_CHECK(mboot_flash_scrub() == 0);
    CORE_CHECK(fake_flash_stats()->writes == 0 && fake_flash_stats()->erases == 0);
}

#endif // MBOOT_ECC_SHADOW

// ---- update audit log ----

static void test_updatelog(void) {
    printf("update audit log\n");
    core_flash_fresh();
    mboot_log_rec_t rec;
    CORE_CHECK(mboot_updatelog_read(0, &rec) == -ENOENT);

    mboot_image_info_t info = {.valid = 1, .ver_major = 2, .ver_minor = 3, .ver_rev = 4, .ver_build = 5, .sec_cnt = 6, .hash_prefix = 0x11223344};
    CORE_CHECK(mboot_updatelog_append(LOG_DFU_BEGIN, MBOOT_RES_OK, SRC_DFU, NULL, 99) == 0);
    CORE_CHECK(mboot_updatelog_append(LOG_IMAGE_ACCEPTED, MBOOT_RES_OK, SRC_DFU, &info, 7) == 0);
    CORE_CHECK(mboot_updatelog_read(0, &rec) == 0);
    CORE_CHECK(rec.type == LOG_IMAGE_ACCEPTED && rec.source == SRC_DFU && rec.seq == 2 && rec.detail == 7);
    CORE_CHECK(rec.ver_major == 2 && rec.ver_minor == 3 && rec.ver_rev == 4 && rec.ver_build == 5 && rec.hash_prefix == 0x11223344);
    CORE_CHECK(mboot_updatelog_read(1, &rec) == 0 && rec.type == LOG_DFU_BEGIN && rec.seq == 1 && rec.detail == 99 && rec.hash_prefix == 0);
    CORE_CHECK(mboot_updatelog_read(2, &rec) == -ENOENT);

    // Ping-pong: more records than one unit holds. The newest records stay readable in order and
    // at least one full unit of history is kept.
    const struct flash_area *log = area(MBOOT_AREA_LOG);
    uint32_t per_unit = mboot_area_erase(log) / sizeof(mboot_log_rec_t);
    uint32_t units = log->fa_size / mboot_area_erase(log);
    uint32_t total = 3 * per_unit * units / 2 + 5;
    for (uint32_t i = 3; i <= total; i++) {
        if (mboot_updatelog_append(LOG_BOOT_OK, MBOOT_RES_OK, SRC_BOOT, NULL, i) != 0) {
            CORE_CHECK(false);
            break;
        }
    }
    uint32_t n = 0;
    uint32_t expect = total;
    while (mboot_updatelog_read(n, &rec) == 0) {
        if (rec.seq != expect || rec.detail != expect) {
            CORE_CHECK(false);
            printf("  record %u has seq %u detail %u, expected %u\n", n, rec.seq, rec.detail, expect);
            break;
        }
        n++;
        expect--;
    }
    CORE_CHECK(n >= per_unit && n <= per_unit * units);
    CORE_CHECK(fake_flash_stats()->overprograms == 0 && fake_flash_stats()->violations == 0);

    // A record torn by a power cut (partly programmed, bad CRC) is skipped; the next append goes
    // into the following slot and the sequence continues.
    core_flash_fresh();
    for (uint32_t i = 1; i <= 5; i++) {
        mboot_updatelog_append(LOG_BOOT_OK, 0, SRC_BOOT, NULL, i);
    }
    uint8_t torn[32];
    memset(torn, 0xFF, sizeof(torn));
    torn[0] = 0x4D;
    torn[1] = 0x42;
    uint32_t slot5 = log->fa_off + 5 * sizeof(mboot_log_rec_t);
    fake_flash_poke(0, slot5, torn, sizeof(torn));
    CORE_CHECK(mboot_updatelog_read(0, &rec) == 0 && rec.seq == 5);
    CORE_CHECK(mboot_updatelog_append(LOG_NO_IMAGE, 0, SRC_BOOT, NULL, 6) == 0);
    CORE_CHECK(mboot_updatelog_read(0, &rec) == 0 && rec.seq == 6 && rec.type == LOG_NO_IMAGE);
    CORE_CHECK(mboot_updatelog_read(1, &rec) == 0 && rec.seq == 5);
    #if defined(MBOOT_ECC_SHADOW)
    // A slot that reads corrected as erased is skipped the same way.
    unit_set(log->fa_off + 7 * sizeof(mboot_log_rec_t), ST_WEAK_ERASED);
    CORE_CHECK(mboot_updatelog_append(LOG_NO_IMAGE, 0, SRC_BOOT, NULL, 7) == 0);
    CORE_CHECK(mboot_updatelog_read(0, &rec) == 0 && rec.seq == 7);
    #endif
    fake_flash_peek(0, log->fa_off + 6 * sizeof(mboot_log_rec_t), torn, 8);
    CORE_CHECK(torn[0] == 0x4D || torn[0] == 0x4E);   // record landed in slot 6

    #if defined(MBOOT_ECC_SHADOW)
    // A record with an ECC-invalid word is also skipped.
    core_flash_fresh();
    for (uint32_t i = 1; i <= 3; i++) {
        mboot_updatelog_append(LOG_BOOT_OK, 0, SRC_BOOT, NULL, i);
    }
    unit_set(log->fa_off + 3 * sizeof(mboot_log_rec_t) + 16, ST_INVALID);
    CORE_CHECK(mboot_updatelog_read(0, &rec) == 0 && rec.seq == 3);
    CORE_CHECK(mboot_updatelog_append(LOG_BOOT_OK, 0, SRC_BOOT, NULL, 4) == 0);
    CORE_CHECK(mboot_updatelog_read(0, &rec) == 0 && rec.seq == 4);
    CORE_CHECK(mboot_updatelog_read(1, &rec) == 0 && rec.seq == 3);
    CORE_CHECK(fake_flash_stats()->overprograms == 1);   // only the unit_set() above
    #endif
    core_flash_fresh();
}

// ---- security counter ----

#if defined(MBOOT_SECCNT_FLASH)
static uint32_t counter_get(void) {
    fih_int c;
    fih_ret r = boot_nv_security_counter_get(0, &c);
    if (FIH_NOT_EQ(r, FIH_SUCCESS)) {
        return UINT32_MAX;
    }
    return (uint32_t)fih_int_decode(c);
}

static uint32_t seccnt_rec_size(void) {
    return mboot_devs[0].write_unit > 16 ? mboot_devs[0].write_unit : 16;
}

// One record as the backend stores it: 'SCNT', image id, value, CRC-32 of those twelve bytes,
// padded with the erased value.
static void seccnt_rec_bytes(uint8_t *buf, uint32_t image_id, uint32_t value) {
    uint32_t w[4] = {MBOOT_SECCNT_REC_MAGIC, image_id, value, 0};
    w[3] = mboot_crc32(0, w, 12);
    memset(buf, mboot_devs[0].erased_val, 32);
    memcpy(buf, w, sizeof(w));
}

static uint32_t seccnt_slot_off(const struct flash_area *sc, uint32_t unit, uint32_t slot) {
    return sc->fa_off + unit * mboot_area_erase(sc) + slot * seccnt_rec_size();
}

static void seccnt_put(const struct flash_area *sc, uint32_t unit, uint32_t slot, const uint8_t *bytes) {
    fake_flash_poke(0, seccnt_slot_off(sc, unit, slot), bytes, seccnt_rec_size());
}

static void test_seccnt(void) {
    printf("security counter (flash)\n");
    core_flash_fresh();
    CORE_CHECK(counter_get() == 0);
    CORE_CHECK(mboot_seccnt_init() == 0);
    CORE_CHECK(fake_flash_stats()->writes == 1);
    CORE_CHECK(mboot_seccnt_init() == 0);
    CORE_CHECK(fake_flash_stats()->writes == 1);      // already initialised
    CORE_CHECK(counter_get() == 0);

    CORE_CHECK(boot_nv_security_counter_update(0, 3) == 0);
    CORE_CHECK(counter_get() == 3);
    CORE_CHECK(boot_nv_security_counter_update(0, 2) == 0);   // lower value: no-op
    CORE_CHECK(counter_get() == 3);
    CORE_CHECK(boot_nv_security_counter_update(0, 3) == 0);
    CORE_CHECK(fake_flash_stats()->writes == 2);
    CORE_CHECK(boot_nv_security_counter_update(0, 10) == 0);
    CORE_CHECK(counter_get() == 10);
    CORE_CHECK(FIH_EQ(boot_nv_security_counter_is_update_possible(0, 11), FIH_SUCCESS));

    // More updates than one unit holds: the value survives the switch to the other unit, in both
    // directions. A unit holds as many records as the erase unit of the area allows (16384 with
    // 256 KiB), so the unit that holds the counter is filled with records by the test, up to three
    // slots from its end, and the updates run across the end.
    const struct flash_area *sc = area(MBOOT_AREA_SECCNT);
    uint32_t rec = seccnt_rec_size();
    uint32_t per_unit = mboot_area_erase(sc) / rec;
    uint32_t v = 10;
    uint32_t used = 3;      // slots of the unit that holds the counter: the first record and two updates
    for (uint32_t round = 0; round < 4; round++) {
        uint8_t bytes[32];
        for (uint32_t slot = used; slot < per_unit - 3; slot++) {
            seccnt_rec_bytes(bytes, 0, ++v);
            seccnt_put(sc, round % 2, slot, bytes);
        }
        CORE_CHECK(counter_get() == v);
        for (uint32_t i = 0; i < 6; i++) {
            v++;
            if (boot_nv_security_counter_update(0, v) != 0 || counter_get() != v) {
                CORE_CHECK(false);
                printf("  update to %u failed in round %u\n", v, (unsigned)round);
                break;
            }
        }
        used = 3;       // the updates past the end of the unit started the other one
    }
    CORE_CHECK(counter_get() == v);
    CORE_CHECK(fake_flash_stats()->erases >= 4);
    CORE_CHECK(fake_flash_stats()->overprograms == 0 && fake_flash_stats()->violations == 0);

    // A torn record (garbage, or an invalid word) never hides the stored value or lowers it.
    uint8_t junk[32];
    memset(junk, 0x00, sizeof(junk));
    core_flash_fresh();
    boot_nv_security_counter_update(0, 5);
    boot_nv_security_counter_update(0, 6);
    fake_flash_poke(0, sc->fa_off + 2 * rec, junk, rec);
    CORE_CHECK(counter_get() == 6);
    CORE_CHECK(boot_nv_security_counter_update(0, 7) == 0 && counter_get() == 7);
    // Another image id does not see the value.
    fih_int c;
    CORE_CHECK(FIH_EQ(boot_nv_security_counter_get(1, &c), FIH_SUCCESS) && fih_int_decode(c) == 0);
    #if defined(MBOOT_ECC_SHADOW)
    unit_set(sc->fa_off + 4 * rec, ST_INVALID);   // slot 3 holds the value 7, slot 4 is free
    CORE_CHECK(counter_get() == 7);
    CORE_CHECK(boot_nv_security_counter_update(0, 8) == 0 && counter_get() == 8);
    // A slot that reads corrected as erased (a cut at the start of its program) is not free: it
    // takes no program, so the record goes to the next slot.
    unit_set(sc->fa_off + 6 * rec, ST_WEAK_ERASED);   // slot 5 holds the value 8, slot 6 is next
    CORE_CHECK(counter_get() == 8);
    CORE_CHECK(boot_nv_security_counter_update(0, 9) == 0 && counter_get() == 9);
    #endif
    core_flash_fresh();
}

// ---- security counter: corrupted area and torn first record ----

static int run_in_child(void (*fn)(void));

#define CASE_CHECK(name, cond) \
    do { \
        if (!(cond)) { \
            core_failures++; \
            printf("  CHECK FAILED seccnt %s: %s\n", name, #cond); \
        } \
    } while (0)

// The area refuses every use: init, read, write and the update check fail with -EIO (a counter
// that cannot be read is the largest possible one), and nothing is written or erased.
static void seccnt_expect_closed(const char *name, const struct flash_area *sc) {
    static uint8_t before[1024 * 1024];
    CASE_CHECK(name, sc->fa_size <= sizeof(before));
    memcpy(before, fake_flash_data(0) + sc->fa_off, sc->fa_size);
    fake_flash_counter_reset();
    uint32_t v = 0x1234;
    CASE_CHECK(name, mboot_seccnt_init() == -EIO);
    CASE_CHECK(name, mboot_seccnt_flash_read(0, &v) == -EIO);
    CASE_CHECK(name, mboot_seccnt_flash_write(0, 1) == -EIO);
    CASE_CHECK(name, mboot_seccnt_flash_write(0, 0) == -EIO);
    CASE_CHECK(name, !mboot_seccnt_flash_can_update(0, 1));
    CASE_CHECK(name, FIH_NOT_EQ(boot_nv_security_counter_init(), FIH_SUCCESS));
    CASE_CHECK(name, counter_get() == UINT32_MAX);
    CASE_CHECK(name, FIH_NOT_EQ(boot_nv_security_counter_is_update_possible(0, 1), FIH_SUCCESS));
    CASE_CHECK(name, boot_nv_security_counter_update(0, 1) == -EIO);
    CASE_CHECK(name, fake_flash_stats()->writes == 0 && fake_flash_stats()->erases == 0);
    CASE_CHECK(name, memcmp(before, fake_flash_data(0) + sc->fa_off, sc->fa_size) == 0);
}

// A torn first record is repaired: unit 0 is erased once and the first record is written, the
// rest of the area is erased, and the counter works again.
static void seccnt_expect_recovered(const char *name, const struct flash_area *sc) {
    uint32_t rec = seccnt_rec_size();
    uint8_t init_rec[32], got[32];
    seccnt_rec_bytes(init_rec, 0, 0);
    fake_flash_counter_reset();
    CASE_CHECK(name, mboot_seccnt_init() == 0);
    CASE_CHECK(name, fake_flash_stats()->erases == 1 && fake_flash_stats()->writes == 1);
    fake_flash_peek(0, seccnt_slot_off(sc, 0, 0), got, rec);
    CASE_CHECK(name, memcmp(got, init_rec, rec) == 0);
    const uint8_t *p = fake_flash_data(0) + seccnt_slot_off(sc, 0, 1);
    bool rest_erased = true;
    for (uint32_t i = 0; i < sc->fa_size - rec; i++) {
        rest_erased = rest_erased && p[i] == mboot_devs[0].erased_val;
    }
    CASE_CHECK(name, rest_erased);
    CASE_CHECK(name, counter_get() == 0);
    CASE_CHECK(name, boot_nv_security_counter_update(0, 4) == 0 && counter_get() == 4);
    CASE_CHECK(name, mboot_seccnt_init() == 0 && counter_get() == 4);
    CASE_CHECK(name, fake_flash_stats()->overprograms == 0 && fake_flash_stats()->violations == 0);
}

static uint32_t child_value;

static void child_seccnt_init(void) {
    mboot_seccnt_init();
}

static void child_seccnt_update(void) {
    boot_nv_security_counter_update(0, child_value);
}

static const uint32_t cut_tears[] = {0, 64, 128, 192, 255};
static const uint32_t cut_seeds[] = {1, 2, 3, 4};

static void test_seccnt_corrupt(void) {
    printf("security counter (corrupted area, torn first record)\n");
    const struct flash_area *sc = area(MBOOT_AREA_SECCNT);
    uint32_t rec = seccnt_rec_size();
    uint32_t last = mboot_area_erase(sc) / rec - 1;
    uint8_t init_rec[32], junk[32], bytes[32];
    uint32_t w[4];
    seccnt_rec_bytes(init_rec, 0, 0);
    memset(junk, 0x00, sizeof(junk));

    // An erased area is the first boot: init stores the first record, value 0.
    core_flash_fresh();
    fake_flash_counter_reset();
    CASE_CHECK("blank", counter_get() == 0);
    CASE_CHECK("blank", mboot_seccnt_flash_can_update(0, 1));
    CASE_CHECK("blank", mboot_seccnt_init() == 0);
    CASE_CHECK("blank", fake_flash_stats()->writes == 1 && fake_flash_stats()->erases == 0);
    fake_flash_peek(0, seccnt_slot_off(sc, 0, 0), bytes, rec);
    CASE_CHECK("blank", memcmp(bytes, init_rec, rec) == 0);
    CASE_CHECK("blank", counter_get() == 0 && mboot_seccnt_init() == 0 && fake_flash_stats()->writes == 1);

    // No valid record in an area that is not erased: the counter is lost or the area is damaged,
    // and the backend does not start a new count from 0.
    core_flash_fresh();
    seccnt_put(sc, 0, 0, junk);
    seccnt_expect_closed("zero slot 0", sc);

    seccnt_rec_bytes(bytes, 0, 0);
    memcpy(w, bytes, sizeof(w));
    w[3] &= w[3] - 1;       // wrong CRC, and a bit cleared that the first record has set
    memcpy(bytes, w, sizeof(w));
    core_flash_fresh();
    seccnt_put(sc, 0, 0, bytes);
    seccnt_expect_closed("bad crc slot 0", sc);

    seccnt_rec_bytes(bytes, 0, 0);
    memcpy(w, bytes, sizeof(w));
    w[0] &= w[0] - 1;       // right CRC for what is there, wrong magic
    w[3] = mboot_crc32(0, w, 12);
    memcpy(bytes, w, sizeof(w));
    core_flash_fresh();
    seccnt_put(sc, 0, 0, bytes);
    seccnt_expect_closed("bad magic slot 0", sc);

    core_flash_fresh();
    seccnt_put(sc, 0, 3, junk);
    seccnt_expect_closed("zero slot 3", sc);

    core_flash_fresh();
    seccnt_put(sc, 0, last, junk);
    seccnt_expect_closed("zero last slot of unit 0", sc);

    core_flash_fresh();
    seccnt_put(sc, 1, 0, junk);
    seccnt_expect_closed("zero slot 0 of unit 1", sc);

    core_flash_fresh();
    for (uint32_t i = 0; i <= last; i++) {
        seccnt_put(sc, 0, i, junk);
    }
    seccnt_expect_closed("unit 0 full of zeros", sc);

    // The records that held the value are destroyed.
    core_flash_fresh();
    mboot_seccnt_init();
    boot_nv_security_counter_update(0, 5);
    boot_nv_security_counter_update(0, 6);
    for (uint32_t i = 0; i < 3; i++) {
        seccnt_put(sc, 0, i, junk);
    }
    seccnt_expect_closed("records destroyed", sc);

    // What a power cut during the very first record leaves is partly programmed bits of that
    // record. Anything beyond exactly that is damage.
    memcpy(bytes, init_rec, sizeof(bytes));
    memset(bytes + 12, mboot_devs[0].erased_val, 4);      // magic, id and value programmed, CRC not
    CASE_CHECK("setup", memcmp(bytes, init_rec, rec) != 0);
    core_flash_fresh();
    seccnt_put(sc, 0, 0, bytes);
    seccnt_put(sc, 0, 1, bytes);
    seccnt_expect_closed("partial first record in slots 0 and 1", sc);
    core_flash_fresh();
    seccnt_put(sc, 0, 1, bytes);
    seccnt_expect_closed("partial first record in slot 1", sc);
    core_flash_fresh();
    seccnt_put(sc, 0, 0, bytes);
    seccnt_put(sc, 1, 0, bytes);
    seccnt_expect_closed("partial first record in both units", sc);
    core_flash_fresh();
    seccnt_put(sc, 0, 0, bytes);
    seccnt_put(sc, 1, 5, junk);
    seccnt_expect_closed("partial first record and zeros in unit 1", sc);
    core_flash_fresh();
    bytes[0] &= (uint8_t) ~0x01;     // a bit programmed that the first record leaves erased
    seccnt_put(sc, 0, 0, bytes);
    CASE_CHECK("setup", init_rec[0] & 0x01);
    seccnt_expect_closed("not a partial first record", sc);
    #if defined(MBOOT_ECC_SHADOW)
    core_flash_fresh();
    unit_set(seccnt_slot_off(sc, 0, 0), ST_INVALID);
    unit_set(seccnt_slot_off(sc, 0, 1), ST_INVALID);
    seccnt_expect_closed("invalid slots 0 and 1", sc);
    core_flash_fresh();
    unit_set(seccnt_slot_off(sc, 0, 1), ST_INVALID);
    seccnt_expect_closed("invalid slot 1", sc);
    core_flash_fresh();
    unit_set(seccnt_slot_off(sc, 0, 0), ST_INVALID);
    unit_set(seccnt_slot_off(sc, 1, 0), ST_INVALID);
    seccnt_expect_closed("invalid slot 0 of both units", sc);
    #endif

    // Garbage next to valid records does not matter.
    core_flash_fresh();
    mboot_seccnt_init();
    boot_nv_security_counter_update(0, 5);
    boot_nv_security_counter_update(0, 6);
    seccnt_put(sc, 0, 10, junk);
    seccnt_put(sc, 0, last, junk);
    seccnt_put(sc, 1, 0, junk);
    seccnt_put(sc, 1, 9, bytes);
    fake_flash_counter_reset();
    CASE_CHECK("garbage beside records", mboot_seccnt_init() == 0 && counter_get() == 6);
    CASE_CHECK("garbage beside records", fake_flash_stats()->writes == 0 && fake_flash_stats()->erases == 0);
    CASE_CHECK("garbage beside records", FIH_EQ(boot_nv_security_counter_is_update_possible(0, 7), FIH_SUCCESS));
    CASE_CHECK("garbage beside records", boot_nv_security_counter_update(0, 7) == 0 && counter_get() == 7);

    // Partial first records are repaired by init.
    memcpy(bytes, init_rec, sizeof(bytes));
    memset(bytes + 12, mboot_devs[0].erased_val, 4);
    core_flash_fresh();
    seccnt_put(sc, 0, 0, bytes);
    seccnt_expect_recovered("partial first record, CRC missing", sc);

    memset(bytes, mboot_devs[0].erased_val, sizeof(bytes));
    memcpy(bytes, init_rec, 4);
    core_flash_fresh();
    seccnt_put(sc, 0, 0, bytes);
    seccnt_expect_recovered("partial first record, magic only", sc);

    memcpy(bytes, init_rec, sizeof(bytes));
    for (unsigned i = 0; i < 16; i++) {
        if (bytes[i] != 0xFF) {
            uint8_t z = (uint8_t) ~bytes[i];
            bytes[i] |= z & (uint8_t)-z;    // the lowest bit that should be programmed is not
            break;
        }
    }
    CASE_CHECK("setup", memcmp(bytes, init_rec, rec) != 0);
    core_flash_fresh();
    seccnt_put(sc, 0, 0, bytes);
    seccnt_expect_recovered("partial first record, one bit short", sc);
    #if defined(MBOOT_ECC_SHADOW)
    core_flash_fresh();
    unit_set(seccnt_slot_off(sc, 0, 0), ST_INVALID);
    seccnt_expect_recovered("invalid first record", sc);
    core_flash_fresh();
    unit_set(seccnt_slot_off(sc, 0, 0), ST_WEAK_ERASED);
    seccnt_expect_recovered("corrected erased first record", sc);
    core_flash_fresh();
    unit_set(seccnt_slot_off(sc, 0, 0), ST_WEAK_DATA);
    seccnt_expect_recovered("corrected first record", sc);
    #endif

    // Power cut inside the write of the first record, every cut model, tear point and seed:
    // whatever it left, the next init gives a working counter that starts at 0.
    unsigned cuts = 0;
    unsigned repaired = 0;
    for (int mode = FAKE_FLASH_CUT_BEFORE; mode <= FAKE_FLASH_CUT_WEAK; mode++) {
        for (unsigned t = 0; t < sizeof(cut_tears) / sizeof(cut_tears[0]); t++) {
            for (unsigned s = 0; s < sizeof(cut_seeds) / sizeof(cut_seeds[0]); s++) {
                char name[64];
                snprintf(name, sizeof(name), "init cut mode %d tear %u seed %u", mode, (unsigned)cut_tears[t], (unsigned)cut_seeds[s]);
                core_flash_fresh();
                fake_flash_counter_reset();
                fake_flash_arm(1, (fake_flash_cut_mode_t)mode, cut_tears[t], cut_seeds[s]);
                run_in_child(child_seccnt_init);
                fake_flash_disarm();
                cuts += fake_flash_cut_fired();
                fake_flash_counter_reset();
                CASE_CHECK(name, mboot_seccnt_init() == 0);
                repaired += fake_flash_stats()->erases;
                CASE_CHECK(name, counter_get() == 0);
                CASE_CHECK(name, boot_nv_security_counter_update(0, 5) == 0 && counter_get() == 5);
                CASE_CHECK(name, fake_flash_stats()->overprograms == 0 && fake_flash_stats()->violations == 0);
            }
        }
    }
    CORE_CHECK(cuts > 0 && repaired > 0);
    printf("  first record: %u cuts, %u repaired by erasing unit 0\n", cuts, repaired);

    // Power cut inside an update: the counter is the old or the new value, never lower, and the
    // next update still works. One run starts with room in the unit; the other with a full unit,
    // so that the update erases the other unit first.
    uint32_t per_unit = mboot_area_erase(sc) / rec;
    for (int full = 0; full < 2; full++) {
        uint32_t old_value = full ? per_unit - 1 : 7;
        uint32_t new_value = old_value + 1;
        for (uint32_t target = 1; target <= (full ? 2u : 1u); target++) {
            for (int mode = FAKE_FLASH_CUT_BEFORE; mode <= FAKE_FLASH_CUT_WEAK; mode++) {
                for (unsigned t = 0; t < sizeof(cut_tears) / sizeof(cut_tears[0]); t++) {
                    for (unsigned s = 0; s < sizeof(cut_seeds) / sizeof(cut_seeds[0]); s++) {
                        char name[96];
                        snprintf(name, sizeof(name), "update %s op %u cut mode %d tear %u seed %u", full ? "of a full unit" : "", (unsigned)target, mode,
                            (unsigned)cut_tears[t], (unsigned)cut_seeds[s]);
                        core_flash_fresh();
                        mboot_seccnt_init();
                        if (full) {
                            // Slot 0 holds the value 0, slot v the value v: a full unit.
                            for (uint32_t v = 1; v <= old_value; v++) {
                                uint8_t rec_bytes[32];
                                seccnt_rec_bytes(rec_bytes, 0, v);
                                seccnt_put(sc, 0, v, rec_bytes);
                            }
                        } else {
                            boot_nv_security_counter_update(0, 3);
                            boot_nv_security_counter_update(0, old_value);
                        }
                        fake_flash_counter_reset();
                        fake_flash_arm(target, (fake_flash_cut_mode_t)mode, cut_tears[t], cut_seeds[s]);
                        child_value = new_value;
                        run_in_child(child_seccnt_update);
                        fake_flash_disarm();
                        fake_flash_counter_reset();
                        uint32_t v = counter_get();
                        CASE_CHECK(name, v == old_value || v == new_value);
                        CASE_CHECK(name, mboot_seccnt_init() == 0 && counter_get() == v);
                        CASE_CHECK(name, boot_nv_security_counter_update(0, new_value) == 0 && counter_get() == new_value);
                        CASE_CHECK(name, boot_nv_security_counter_update(0, old_value) == 0 && counter_get() == new_value);
                        CASE_CHECK(name, fake_flash_stats()->overprograms == 0 && fake_flash_stats()->violations == 0);
                    }
                }
            }
        }
    }
    core_flash_fresh();
}
#endif

// ---- request handoff ----

static int run_in_child(void (*fn)(void)) {
    fflush(stdout);
    pid_t pid = fork();
    if (pid == 0) {
        fn();
        _exit(0);
    }
    int status = 0;
    waitpid(pid, &status, 0);
    return WIFEXITED(status) ? WEXITSTATUS(status) : -1;
}

static const uint8_t k_elems[] = {2, 10, 1, 0, 0, 0, 8, 0, 0, 0, 0, 0, 4, 2, 3, 1, 0, 0};

static void child_request_fsload(void) {
    mboot_request_set_and_reset(MBOOT_REQ_FSLOAD, k_elems, sizeof(k_elems));
}

static void child_request_dfu(void) {
    mboot_request_set_and_reset(MBOOT_REQ_DFU, NULL, 0);
}

static void child_request_too_long(void) {
    static uint8_t big[MBOOT_REQ_ELEMS_MAX + 1];
    mboot_request_set_and_reset(MBOOT_REQ_FSLOAD, big, sizeof(big));
}

static void test_request(void) {
    printf("request handoff\n");
    core_flash_fresh();
    uint32_t *fi = (uint32_t *)(core_shared->req_ram + 0x3F0);
    mboot_request_t req;

    // fsload request set by the app, taken by the bootloader after a soft reset.
    memset(core_shared->req_ram, 0xA5, sizeof(core_shared->req_ram));
    fi[0] = 0x4A4E4946u;
    fi[1] = 5;
    int st = run_in_child(child_request_fsload);
    CORE_CHECK(st == CORE_EXIT_RESET);
    CORE_CHECK(core_shared->retention == (MBOOT_RET_KEY | MBOOT_RET_FSLOAD) || MBOOT_RETENTION_BITS < 32);
    mboot_request_take(&req, MBOOT_RESET_SOFT);
    CORE_CHECK(req.mode == MBOOT_REQ_FSLOAD && req.elems_len == sizeof(k_elems) && memcmp(req.elems, k_elems, sizeof(k_elems)) == 0);
    CORE_CHECK(req.magic == MBOOT_REQ_MAGIC && req.version == MBOOT_REQ_VERSION && req.seq >= 1);
    // One shot: the request and the retention word are gone and the test state is kept.
    CORE_CHECK(core_shared->retention == 0);
    bool zero = true;
    for (size_t i = 0; i < sizeof(mboot_request_t); i++) {
        zero = zero && core_shared->req_ram[i] == 0;
    }
    CORE_CHECK(zero);
    CORE_CHECK(fi[0] == 0x4A4E4946u && fi[1] == 5);
    mboot_request_take(&req, MBOOT_RESET_SOFT);
    CORE_CHECK(req.mode == MBOOT_REQ_NONE);

    // Plain DFU request.
    st = run_in_child(child_request_dfu);
    CORE_CHECK(st == CORE_EXIT_RESET);
    mboot_request_take(&req, MBOOT_RESET_PIN);
    CORE_CHECK(req.mode == MBOOT_REQ_DFU && req.elems_len == 0);

    // Too many elements degrade to a DFU request without elements.
    st = run_in_child(child_request_too_long);
    CORE_CHECK(st == CORE_EXIT_RESET);
    mboot_request_take(&req, MBOOT_RESET_SOFT);
    CORE_CHECK(req.mode == MBOOT_REQ_DFU && req.elems_len == 0);

    // Corrupt requests are refused; with a matching retention word the result is a DFU request.
    run_in_child(child_request_fsload);
    core_shared->req_ram[22] ^= 0x01;
    core_shared->retention = 0;
    mboot_request_take(&req, MBOOT_RESET_SOFT);
    CORE_CHECK(req.mode == MBOOT_REQ_NONE);
    run_in_child(child_request_fsload);
    core_shared->req_ram[22] ^= 0x01;
    mboot_request_take(&req, MBOOT_RESET_SOFT);
    CORE_CHECK(req.mode == MBOOT_REQ_DFU && req.elems_len == 0);
    CORE_CHECK(core_shared->retention == 0);

    // Header fields: bad magic, bad version, length over the limit, mode out of range.
    for (int variant = 0; variant < 4; variant++) {
        run_in_child(child_request_fsload);
        core_shared->retention = 0;
        mboot_request_t *r = (mboot_request_t *)core_shared->req_ram;
        switch (variant) {
            case 0:
                r->magic ^= 1;
                break;
            case 1:
                r->version = 2;
                break;
            case 2:
                r->elems_len = MBOOT_REQ_ELEMS_MAX + 1;
                break;
            case 3:
                r->mode = 3;
                break;
        }
        // keep the CRC consistent where the field is covered, so that only the field check refuses it
        if (variant != 0 && variant != 2) {
            r->crc32 = 0;
            uint32_t crc = mboot_crc32(0, r, offsetof(mboot_request_t, elems));
            r->crc32 = mboot_crc32(crc, r->elems, r->elems_len);
        }
        mboot_request_take(&req, MBOOT_RESET_SOFT);
        CORE_CHECK(req.mode == MBOOT_REQ_NONE);
    }

    // After power-on the region is initialised, whatever it held, including the test state;
    // a stale retention word is cleared too.
    run_in_child(child_request_fsload);
    mboot_request_take(&req, MBOOT_RESET_POR | MBOOT_RESET_PIN);
    CORE_CHECK(req.mode == MBOOT_REQ_NONE && core_shared->retention == 0);
    zero = true;
    for (size_t i = 0; i < sizeof(core_shared->req_ram); i++) {
        zero = zero && core_shared->req_ram[i] == 0;
    }
    CORE_CHECK(zero);

    // A bare retention key with no request struct.
    core_shared->retention = MBOOT_RET_KEY | MBOOT_RET_FSLOAD;
    mboot_request_take(&req, MBOOT_RESET_SOFT);
    #if MBOOT_RETENTION_BITS >= 32
    CORE_CHECK(req.mode == MBOOT_REQ_DFU && req.elems_len == 0);
    #endif
    core_shared->retention = 0x70AD0000u ^ 0x00010000u;
    mboot_request_take(&req, MBOOT_RESET_SOFT);
    CORE_CHECK(req.mode == MBOOT_REQ_NONE);
    core_flash_fresh();
}

// ---- fault injection counter ----

static void test_fi_counter(void) {
    printf("fault injection counter\n");
    core_flash_fresh();
    uint32_t *fi = (uint32_t *)(core_shared->req_ram + 0x3F0);
    const struct flash_area *sec = area(FLASH_AREA_IMAGE_SECONDARY(0));
    uint8_t buf[32] = {1, 2, 3};

    memset(fi, 0, 16);
    fake_flash_counter_reset();
    flash_area_erase(sec, 0, mboot_area_erase(sec));
    CORE_CHECK(fi[2] == 0);       // not armed: the counter does not move

    fi[0] = 0x4A4E4946u;
    fi[1] = 0;
    fi[2] = 0;
    fake_flash_counter_reset();
    flash_area_erase(sec, 0, mboot_area_erase(sec));
    flash_area_write(sec, 0, buf, mboot_devs[0].write_unit);
    flash_area_write(sec, mboot_devs[0].write_unit, buf, mboot_devs[0].write_unit);
    CORE_CHECK(fi[2] == 3 && fake_flash_op_count() == 3);
    memset(fi, 0, 16);
}

int core_unit_run(void) {
    core_failures = 0;
    test_printf();
    test_crc32();
    test_flash_map();
    test_policy_areas();
    test_erase_trailer();
    #if defined(MBOOT_ECC_SHADOW)
    test_shadow_read_rule();
    test_shadow_header_window();
    test_shadow_write_rule();
    test_shadow_erase_scrub();
    #endif
    test_updatelog();
    #if defined(MBOOT_SECCNT_FLASH)
    test_seccnt();
    test_seccnt_corrupt();
    #endif
    test_request();
    test_fi_counter();
    printf("unit tests: %s (%d failed checks)\n", core_failures == 0 ? "PASS" : "FAIL", core_failures);
    return core_failures;
}
