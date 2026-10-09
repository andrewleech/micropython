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
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/wait.h>
#include <unistd.h>

#include "bootutil/bootutil.h"
#include "bootutil/bootutil_public.h"
#include "bootutil/image.h"
#include "flash_map_backend/flash_map_backend.h"
#include "mboot_dfu.h"
#include "mboot_elem.h"
#include "mboot_fsload.h"
#include "mcuboot_config/mcuboot_config.h"
#include "mboot_crc32.h"
#include "mboot_dfu_recovery.h"
#include "mboot_port.h"
#include "mboot_request.h"
#include "mboot_types.h"
#include "mboot_validate.h"
#include "sysflash/sysflash.h"
#include "tusb.h"
#include "fake_flash.h"
#include "spi_nor/spi_nor.h"
#include "bl.h"

int mboot_main(void);

#if MBOOT_POLICY_SINGLE
// The single slot policy has no secondary slot: the tests of the swap and overwrite policies are
// compiled but not listed in tests[].
#pragma GCC diagnostic ignored "-Wunused-function"
#endif

// Tests of the bootloader main flow on the fake flash. Each boot attempt runs the real
// mboot_main() in a forked child (see bl.h). The DFU front end is the real dfu_glue.c and DFU
// core over a fake TinyUSB; the harness plays the host.

#if defined(MCUBOOT_HW_ROLLBACK_PROT)
#define ROLLBACK_COUNTER 1
#else
#define ROLLBACK_COUNTER 0
#endif

// ---- checks ----

static int n_checks;
static int n_failures;
static bool verbose;
static bool echo_log;
static bool nor_scatter;     // a cut inside a SPI NOR command leaves the whole page or sector undefined

static bool check(bool cond, const char *expr, const char *file, int line) {
    n_checks++;
    if (!cond) {
        n_failures++;
        printf("  FAIL %s:%d: %s\n", file, line, expr);
    }
    return cond;
}

#define CHECK(cond) check((cond), #cond, __FILE__, __LINE__)

// The slot policy of the board. Swap installs an update as a test image that reverts unless it is
// confirmed. Overwrite-external copies it over the primary slot for good. Single writes it over the
// only slot: there is no secondary slot, no pending state and no old image to fall back to.
#define SPARE (MBOOT_UPDATE_SPARE)
#define SWAP_AFTER_UPDATE (MBOOT_POLICY_SWAP ? BOOT_SWAP_TYPE_REVERT : BOOT_SWAP_TYPE_NONE)

// ---- images ----

typedef struct {
    uint8_t *data;
    size_t len;
} blob_t;

static const char *img_dir = "images";

static blob_t load_blob(const char *dir, const char *name) {
    char path[512];
    snprintf(path, sizeof(path), "%s/%s", dir, name);
    blob_t b = {NULL, 0};
    FILE *f = fopen(path, "rb");
    if (f == NULL) {
        printf("cannot open %s\n", path);
        exit(2);
    }
    fseek(f, 0, SEEK_END);
    b.len = (size_t)ftell(f);
    fseek(f, 0, SEEK_SET);
    b.data = malloc(b.len);
    if (b.data == NULL || fread(b.data, 1, b.len, f) != b.len) {
        printf("cannot read %s\n", path);
        exit(2);
    }
    fclose(f);
    return b;
}

static blob_t v1_initial;   // version 1.0.0, counter 1, the image of the primary slot, confirmed
static blob_t v2_initial;   // version 2.0.0, counter 2, the image of the primary slot, confirmed
static blob_t v2;           // update image 2.0.0, counter 2
static blob_t v2_same_counter;  // update image 2.0.0, counter 1 (the counter of v1)
static blob_t v3;           // update image 3.0.0, counter 3

typedef struct {
    const char *name;
    uint16_t codes[2];      // result codes the validator may answer (0 terminates)
} variant_t;

// The negative variants of tests/mboot/make_variants.py with the result codes the validator may answer.
// oversize, old_version and old_counter have their own cases.
static const variant_t variants[] = {
    {"bad_sig", {MBOOT_RES_ERR_SIG, 0}},
    {"bad_hash", {MBOOT_RES_ERR_HASH, 0}},
    {"wrong_key", {MBOOT_RES_ERR_SIG, 0}},
    {"truncated_half", {MBOOT_RES_ERR_HEADER, MBOOT_RES_ERR_SIG}},
    {"truncated_tlv", {MBOOT_RES_ERR_HEADER, MBOOT_RES_ERR_SIG}},
    {"bad_header_magic", {MBOOT_RES_ERR_HEADER, 0}},
    {"wrong_layout", {MBOOT_RES_ERR_LAYOUT, 0}},
    {"no_layout", {MBOOT_RES_ERR_LAYOUT, 0}},
};
#define N_VARIANTS (sizeof(variants) / sizeof(variants[0]))

static blob_t variant_blob[N_VARIANTS];
static blob_t good_blob, oversize_blob, old_version_blob, old_counter_blob;
static blob_t small_blob;   // a valid update image of less than one erase unit (version 2.0.0, counter 2)

static void load_images(void) {
    v1_initial = load_blob(img_dir, "v1_initial.bin");
    v2_initial = load_blob(img_dir, "v2_initial.bin");
    v2 = load_blob(img_dir, "v2.bin");
    v2_same_counter = load_blob(img_dir, "v2_same_counter.bin");
    v3 = load_blob(img_dir, "v3.bin");
    small_blob = load_blob(img_dir, "small.bin");
    char dir[512];
    snprintf(dir, sizeof(dir), "%s/variants", img_dir);
    for (size_t i = 0; i < N_VARIANTS; i++) {
        char name[64];
        snprintf(name, sizeof(name), "%s.bin", variants[i].name);
        variant_blob[i] = load_blob(dir, name);
    }
    good_blob = load_blob(dir, "good.bin");
    oversize_blob = load_blob(dir, "oversize.bin");
    old_version_blob = load_blob(dir, "old_version.bin");
    old_counter_blob = load_blob(dir, "old_counter.bin");
}

// ---- flash state ----

static const struct flash_area *area(uint8_t id) {
    const struct flash_area *fa = NULL;
    if (flash_area_open(id, &fa) != 0) {
        printf("no area %u\n", id);
        exit(2);
    }
    return fa;
}

// Erase unit of the slots.
static uint32_t eu(void) {
    return mboot_area_erase(area(FLASH_AREA_IMAGE_PRIMARY(0)));
}

// Creates the devices described by the generated device table. Device 0 is the internal flash
// (it tears words: ECC); a device that is not memory mapped is a SPI NOR chip behind the real
// drivers/memory/spiflash.c (spi_nor/). dev0_size replaces the size of device 0 when not 0.
static void flash_devices(uint32_t dev0_size) {
    fake_flash_dev_cfg_t cfg[FAKE_FLASH_MAX_DEVS];
    memset(cfg, 0, sizeof(cfg));
    for (unsigned i = 0; i < mboot_dev_count; i++) {
        const mboot_flash_dev_t *d = &mboot_devs[i];
        if (i != 0 && !d->mapped) {
            spi_nor_dev_cfg(&cfg[i], d->size, nor_scatter);
            continue;
        }
        cfg[i].size = i == 0 && dev0_size != 0 ? dev0_size : d->size;
        // The runs of the table, cut off at the size of the device.
        uint32_t left = cfg[i].size;
        for (unsigned j = 0; j < d->run_count && left != 0; j++) {
            uint32_t size = d->runs[j].size < left ? d->runs[j].size : left;
            cfg[i].runs[cfg[i].n_runs].size = size;
            cfg[i].runs[cfg[i].n_runs].erase = d->runs[j].erase;
            cfg[i].n_runs++;
            left -= size;
        }
        cfg[i].write_unit = d->write_unit;
        cfg[i].erased_val = d->erased_val;
        cfg[i].ecc = i == 0;
    }
    fake_flash_init(mboot_dev_count, cfg);
    for (unsigned i = 1; i < mboot_dev_count; i++) {
        if (!mboot_devs[i].mapped) {
            CHECK(host_port_flash_attach_spi_nor(i) == 0);
        }
    }
}

// Fresh flash described by the generated device table, fresh shared state.
static void flash_fresh(void) {
    flash_devices(0);
    memset(bl_shared, 0, sizeof(*bl_shared));
    bl_shared->reset_cause = MBOOT_RESET_SOFT;
    bl_shared->log_echo = echo_log;
}

// The padded image of the primary slot (trailer magic and image_ok included).
static void put_primary(const blob_t *img) {
    const struct flash_area *pri = area(FLASH_AREA_IMAGE_PRIMARY(0));
    fake_flash_poke(pri->fa_device_id, pri->fa_off, img->data, (uint32_t)img->len);
}

// An update image where DFU puts it: after the spare sector of the secondary slot.
static void put_update(const blob_t *img) {
    const struct flash_area *sec = area(FLASH_AREA_IMAGE_SECONDARY(0));
    fake_flash_poke(sec->fa_device_id, sec->fa_off + SPARE, img->data, (uint32_t)img->len);
}

// Where the FAT volume with the update lives on the SPI flash (device 1): the filesystem area when
// the board puts it there, otherwise the first erase unit after the areas of the device.
static uint32_t file_off(void) {
    uint32_t off = 0;
    for (unsigned i = 0; i < mboot_area_count; i++) {
        const struct flash_area *fa = &mboot_areas[i];
        if (fa->fa_device_id != 1) {
            continue;
        }
        if (fa->fa_id == MBOOT_AREA_FS) {
            return fa->fa_off;
        }
        off = fa->fa_off + fa->fa_size > off ? fa->fa_off + fa->fa_size : off;
    }
    return (off + eu() - 1) / eu() * eu();
}

#define FILE_BASE (mboot_devs[1].base + file_off())

// The FAT volume that holds the named variant as /update.bin (made by mkfat.py), at file_off() of
// the SPI flash. Returns the length of the volume.
static uint32_t put_volume(const char *name, const char *suffix) {
    char dir[512];
    char file[64];
    snprintf(dir, sizeof(dir), "%s/fat", img_dir);
    snprintf(file, sizeof(file), "%s%s", name, suffix);
    blob_t vol = load_blob(dir, file);
    fake_flash_poke(1, file_off(), vol.data, (uint32_t)vol.len);
    free(vol.data);
    return (uint32_t)vol.len;
}

static uint32_t put_file(const char *name) {
    return put_volume(name, ".fat");
}

#if defined(MBOOT_FSLOAD_RAW)
// The file itself in the raw window at file_off() of the SPI flash. Returns the length of the
// window, the file rounded up to the erase unit.
static uint32_t put_raw(const blob_t *img, uint32_t off) {
    fake_flash_poke(1, off, img->data, (uint32_t)img->len);
    return (uint32_t)((img->len + eu() - 1) / eu() * eu());
}
#endif

static uint32_t crc_range(uint8_t dev, uint32_t off, uint32_t len) {
    return mboot_crc32(0, fake_flash_data(dev) + off, len);
}

static uint32_t crc_area(uint8_t id) {
    const struct flash_area *fa = area(id);
    return crc_range(fa->fa_device_id, fa->fa_off, fa->fa_size);
}

// CRC over the internal flash and, optionally, the secondary slot with its half of the shadow
// area (the shadow words of the secondary trailer follow the writes DFU makes there).
static uint32_t crc_flash_except(bool secondary_too) {
    const struct flash_area *sec = area(FLASH_AREA_IMAGE_SECONDARY(0));
    // A flash without ECC has no shadow area.
    const struct flash_area *shadow = mboot_flash_area_find(MBOOT_AREA_SHADOW);
    struct {
        uint32_t off;
        uint32_t end;
    } skip[2] = {{0, 0}, {0, 0}};
    // A secondary slot on another device is not part of the internal flash and has no shadow words.
    if (secondary_too && sec->fa_device_id == 0) {
        skip[0].off = sec->fa_off;
        skip[0].end = sec->fa_off + sec->fa_size;
        if (shadow != NULL) {
            skip[1].off = shadow->fa_off + shadow->fa_size / 2;
            skip[1].end = shadow->fa_off + shadow->fa_size;
        }
    }
    uint32_t crc = 0;
    uint32_t pos = 0;
    uint32_t size = mboot_devs[0].size;
    while (pos < size) {
        bool skipped = false;
        uint32_t end = size;
        for (int i = 0; i < 2; i++) {
            if (skip[i].end == 0) {
                continue;
            }
            if (pos >= skip[i].off && pos < skip[i].end) {
                pos = skip[i].end;
                skipped = true;
                break;
            }
            if (pos < skip[i].off && skip[i].off < end) {
                end = skip[i].off;
            }
        }
        if (skipped) {
            continue;
        }
        crc = mboot_crc32(crc, fake_flash_data(0) + pos, end - pos);
        pos = end;
    }
    return crc;
}

static bool is_erased(uint8_t dev, uint32_t off, uint32_t len) {
    const uint8_t *p = fake_flash_data(dev) + off;
    for (uint32_t i = 0; i < len; i++) {
        if (p[i] != mboot_devs[dev].erased_val) {
            return false;
        }
    }
    return true;
}


// ---- children ----

typedef struct {
    bool hang;
    bool crash;
    int signal;
    int status;
    uint32_t ops;
    bl_out_t out;
} run_t;

static void act_main(void) {
    mboot_main();
    _exit(99);
}

static void act_request_dfu(void) {
    mboot_request_set_and_reset(MBOOT_REQ_DFU, NULL, 0);
}

static uint8_t g_elems[256];
static size_t g_elems_len;

static void act_request_fsload(void) {
    mboot_request_set_and_reset(MBOOT_REQ_FSLOAD, g_elems, g_elems_len);
}

static void act_set_pending(void) {
    bl_shared->out.rc = boot_set_pending_multi(0, 0);
    bl_shared->out.kind = BL_OUT_DONE;
    _exit(BL_EXIT_DONE);
}

#if MBOOT_POLICY_SWAP
static void act_confirm(void) {
    bl_shared->out.rc = boot_set_confirmed_multi(0);
    bl_shared->out.kind = BL_OUT_DONE;
    _exit(BL_EXIT_DONE);
}
#endif

static run_t run_child(void (*fn)(void)) {
    memset(&bl_shared->out, 0, sizeof(bl_shared->out));
    fflush(stdout);
    uint32_t ops0 = fake_flash_op_count();
    pid_t pid = fork();
    if (pid == 0) {
        alarm(60);
        fn();
        _exit(98);
    }
    int status = 0;
    waitpid(pid, &status, 0);
    run_t r;
    memset(&r, 0, sizeof(r));
    r.out = bl_shared->out;
    r.ops = fake_flash_op_count() - ops0;
    if (WIFEXITED(status)) {
        r.status = WEXITSTATUS(status);
        if (r.status < BL_EXIT_JUMP && r.status != 0 && r.status != FAKE_FLASH_CUT_EXIT) {
            r.crash = true;
        }
        if (r.status > BL_EXIT_DONE) {
            r.crash = true;
        }
    } else if (WIFSIGNALED(status)) {
        r.signal = WTERMSIG(status);
        r.hang = r.signal == SIGALRM;
        r.crash = !r.hang;
    }
    return r;
}

static const char *describe(const run_t *r) {
    static char buf[200];
    if (r->hang) {
        return "hang";
    }
    if (r->crash) {
        snprintf(buf, sizeof(buf), "crash (status %d, signal %d)", r->status, r->signal);
        return buf;
    }
    switch (r->out.kind) {
        case BL_OUT_JUMP:
            snprintf(buf, sizeof(buf), "jump to v%u.%u.%u swap type %d", r->out.running.ver_major, r->out.running.ver_minor, r->out.running.ver_rev,
                r->out.swap_type);
            return buf;
        case BL_OUT_RESET:
            return r->out.dfu.entered ? "reset after DFU" : "reset";
        case BL_OUT_DFU_WAIT:
            snprintf(buf, sizeof(buf), "waiting in DFU (cause %d)", r->out.dfu.why);
            return buf;
        case BL_OUT_DONE:
            snprintf(buf, sizeof(buf), "done rc=%d", r->out.rc);
            return buf;
        default:
            return "no outcome";
    }
}

static bool jumped(const run_t *r) {
    return !r->hang && !r->crash && r->out.kind == BL_OUT_JUMP;
}

static bool jumped_to(const run_t *r, uint8_t major) {
    return jumped(r) && r->out.running.valid && r->out.running.ver_major == major;
}

// The DFU front end started in the child.
static bool in_dfu(const run_t *r) {
    return !r->hang && !r->crash && r->out.dfu.entered;
}

static run_t boot(void) {
    return run_child(act_main);
}

// ---- the application and the host ----

static bool app_request_dfu(void) {
    run_t r = run_child(act_request_dfu);
    return !r.crash && !r.hang && r.out.kind == BL_OUT_RESET;
}

static size_t put_elem(uint8_t *p, uint8_t type, const void *payload, size_t len) {
    p[0] = type;
    p[1] = (uint8_t)len;
    memcpy(p + 2, payload, len);
    return 2 + len;
}

// MOUNT of the FAT volume [FILE_BASE, FILE_BASE + window_len) and FSLOAD of /update.bin, then END.
static void build_fsload(uint32_t window_len) {
    uint8_t mount[10] = {1, MBOOT_FSLOAD_FS_FAT};
    uint32_t base = FILE_BASE;
    memcpy(mount + 2, &base, 4);
    memcpy(mount + 6, &window_len, 4);
    uint8_t path[12] = {1, '/', 'u', 'p', 'd', 'a', 't', 'e', '.', 'b', 'i', 'n'};
    size_t n = 0;
    n += put_elem(g_elems + n, MBOOT_ELEM_TYPE_MOUNT, mount, sizeof(mount));
    n += put_elem(g_elems + n, MBOOT_ELEM_TYPE_FSLOAD, path, sizeof(path));
    n += put_elem(g_elems + n, MBOOT_ELEM_TYPE_END, NULL, 0);
    g_elems_len = n;
}

static bool request_reset(void) {
    run_t r = run_child(act_request_fsload);
    return !r.crash && !r.hang && r.out.kind == BL_OUT_RESET;
}

static bool app_request_fsload(const char *name) {
    build_fsload(put_file(name));
    return request_reset();
}

#if defined(MBOOT_FSLOAD_GZIP)
// The FAT volume that holds the gzip compressed named variant as /update.bin.
static bool app_request_fsload_gz(const char *name) {
    build_fsload(put_volume(name, ".gz.fat"));
    return request_reset();
}
#endif

#if defined(MBOOT_FSLOAD_RAW)
// MOUNT of the raw window [base, base + len), with a second window [base2, base2 + len2) when
// len2 is not 0 (the 18 byte form), and FSLOAD of a path that is ignored, then END.
static void build_fsload_raw(uint32_t base, uint32_t len, uint32_t base2, uint32_t len2) {
    uint8_t mount[18] = {1, MBOOT_FSLOAD_FS_RAW};
    memcpy(mount + 2, &base, 4);
    memcpy(mount + 6, &len, 4);
    memcpy(mount + 10, &base2, 4);
    memcpy(mount + 14, &len2, 4);
    uint8_t path[2] = {1, 'x'};
    size_t n = 0;
    n += put_elem(g_elems + n, MBOOT_ELEM_TYPE_MOUNT, mount, len2 != 0 ? sizeof(mount) : 10);
    n += put_elem(g_elems + n, MBOOT_ELEM_TYPE_FSLOAD, path, sizeof(path));
    n += put_elem(g_elems + n, MBOOT_ELEM_TYPE_END, NULL, 0);
    g_elems_len = n;
}

static bool app_request_fsload_raw(const blob_t *img) {
    build_fsload_raw(FILE_BASE, put_raw(img, file_off()), 0, 0);
    return request_reset();
}
#endif

// The host of a DFU session: the application asked for DFU, the bootloader starts the front
// end, the host sends img and ends the download. max_blocks 0 sends everything.
static run_t dfu_session(const blob_t *img, unsigned max_blocks, bool omit_manifest) {
    CHECK(app_request_dfu());
    bl_shared->script.kind = BL_SCRIPT_INSTALL;
    bl_shared->script.blob = img->data;
    bl_shared->script.len = img->len;
    bl_shared->script.max_blocks = max_blocks;
    bl_shared->script.omit_manifest = omit_manifest;
    run_t r = boot();
    memset(&bl_shared->script, 0, sizeof(bl_shared->script));
    return r;
}

static uint8_t dfu_status_for(uint16_t code) {
    switch (code) {
        case MBOOT_RES_ERR_HEADER:
        case MBOOT_RES_ERR_TOO_BIG:
            return DFU_STATUS_ERR_ADDRESS;
        default:
            return DFU_STATUS_ERR_FILE;
    }
}

static bool code_in(uint16_t code, const uint16_t *codes) {
    return code == codes[0] || (codes[1] != 0 && code == codes[1]);
}

// ---- cases ----

// Boots the image of the primary slot once so that the scrub and the security counter have
// settled, and checks that it runs.
static void settled_primary(const blob_t *img, uint8_t major) {
    flash_fresh();
    put_primary(img);
    run_t r = boot();
    CHECK(jumped_to(&r, major));
    CHECK(r.out.swap_type == BOOT_SWAP_TYPE_NONE);
}

// Good update through the real main flow: a good image over DFU is swapped in as a test image,
// reverts at the next reset unless the application confirms it.
static void test_good_install(void) {
    printf("good image over DFU: test swap, revert, confirm\n");
    settled_primary(&v1_initial, 1);

    run_t r = dfu_session(&good_blob, 0, false);
    CHECK(in_dfu(&r));
    CHECK(r.out.dfu.why == REC_APP_REQUEST);
    CHECK(r.out.dfu.manifest_status == DFU_STATUS_OK);
    CHECK(r.out.dfu.result.code == MBOOT_RES_OK && r.out.dfu.result.phase == MBOOT_DFU_PHASE_VALIDATE);
    CHECK(r.out.kind == BL_OUT_RESET);

    r = boot();
    CHECK(jumped_to(&r, 2));
    CHECK(r.out.swap_type == SWAP_AFTER_UPDATE);    // a test image, reverts unless confirmed

    #if MBOOT_POLICY_SWAP
    r = boot();
    CHECK(jumped_to(&r, 1));
    CHECK(r.out.swap_type == BOOT_SWAP_TYPE_NONE);
    #else
    // The update is final: the next boot runs it again.
    r = boot();
    CHECK(jumped_to(&r, 2));
    CHECK(r.out.swap_type == BOOT_SWAP_TYPE_NONE);
    #endif

    // The same image again, this time confirmed.
    #if MBOOT_POLICY_SWAP
    r = dfu_session(&good_blob, 0, false);
    CHECK(r.out.dfu.manifest_status == DFU_STATUS_OK);
    r = boot();
    CHECK(jumped_to(&r, 2));
    run_t c = run_child(act_confirm);
    CHECK(c.out.kind == BL_OUT_DONE && c.out.rc == 0);
    #endif
    r = boot();
    CHECK(jumped_to(&r, 2));
    CHECK(r.out.swap_type == BOOT_SWAP_TYPE_NONE);
    r = boot();
    CHECK(jumped_to(&r, 2));
    CHECK(fake_flash_stats()->violations == 0);
}

// Negative images over DFU are validated at the manifest step and leave the primary slot unchanged.
static void test_dfu_rejections(void) {
    printf("negative images over DFU\n");
    for (size_t i = 0; i < N_VARIANTS; i++) {
        settled_primary(&v1_initial, 1);
        uint32_t pri_crc = crc_area(FLASH_AREA_IMAGE_PRIMARY(0));
        uint32_t rest_crc = crc_flash_except(true);

        run_t r = dfu_session(&variant_blob[i], 0, false);
        const bl_dfu_out_t *d = &r.out.dfu;
        bool ok = CHECK(in_dfu(&r)) && CHECK(r.out.kind == BL_OUT_DFU_WAIT);
        if (!ok || !CHECK(d->manifest_status != 0xFF)) {
            printf("    %s: %s\n", variants[i].name, describe(&r));
            continue;
        }
        if (!CHECK(code_in(d->result.code, variants[i].codes))) {
            printf("    %s: result code %u\n", variants[i].name, d->result.code);
        }
        if (verbose) {
            printf("    %-18s dfu status %02x code %u\n", variants[i].name, d->manifest_status, d->result.code);
        }
        CHECK(d->manifest_status == dfu_status_for(d->result.code));
        CHECK(d->result.phase == MBOOT_DFU_PHASE_VALIDATE);
        const struct flash_area *sec = area(FLASH_AREA_IMAGE_SECONDARY(0));
        CHECK(is_erased(sec->fa_device_id, sec->fa_off + SPARE, 64));    // the header is gone
        CHECK(crc_area(FLASH_AREA_IMAGE_PRIMARY(0)) == pri_crc);
        CHECK(crc_flash_except(true) == rest_crc);

        // Nothing is pending: the next boot runs the old image without a swap.
        r = boot();
        CHECK(jumped_to(&r, 1));
        CHECK(r.out.swap_type == BOOT_SWAP_TYPE_NONE);
    }
}

// Oversize image: an image larger than the update slot is refused at the first block past the region and
// nothing outside the update slot changes.
static void test_dfu_oversize(void) {
    printf("oversize image over DFU\n");
    settled_primary(&v1_initial, 1);
    uint32_t rest_crc = crc_flash_except(true);
    run_t r = dfu_session(&oversize_blob, 0, false);
    CHECK(in_dfu(&r));
    CHECK(r.out.dfu.block_status == DFU_STATUS_ERR_ADDRESS);
    CHECK(r.out.dfu.manifest_status == 0xFF);
    CHECK(crc_flash_except(true) == rest_crc);
    r = boot();
    CHECK(jumped_to(&r, 1));
    CHECK(r.out.swap_type == BOOT_SWAP_TYPE_NONE);
}

// A host that writes the trailer of the secondary slot itself (the boot magic at its end) and
// resets without a manifest would get a validly signed image swapped in with none of the checks
// of the manifest step. The trailer is outside the DFU region: the first block that addresses it
// is refused, nothing of it is written, and the next boot runs the old image.
static void test_dfu_trailer_forged(void) {
    printf("DFU cannot write the secondary trailer\n");
    static const uint8_t boot_magic[16] = {
        0x77, 0xc2, 0x95, 0xf3, 0x60, 0xd2, 0xef, 0x7f, 0x35, 0x52, 0x50, 0x0f, 0x2c, 0xb6, 0x79, 0x80
    };
    settled_primary(&v1_initial, 1);
    const struct flash_area *sec = area(FLASH_AREA_IMAGE_SECONDARY(0));
    uint32_t trailer_off;
    CHECK(mboot_flash_slot_trailer_off(sec, &trailer_off) == 0);
    uint32_t spare = SPARE;
    blob_t forged = {malloc(sec->fa_size - spare), sec->fa_size - spare};
    CHECK(forged.data != NULL);
    memset(forged.data, 0xFF, forged.len);
    memcpy(forged.data, v2.data, v2.len);
    memcpy(forged.data + forged.len - sizeof(boot_magic), boot_magic, sizeof(boot_magic));

    run_t r = dfu_session(&forged, 0, true);
    CHECK(in_dfu(&r));
    CHECK(r.out.dfu.block_status == DFU_STATUS_ERR_ADDRESS);
    // The blocks of the image area were accepted, the first block of the trailer was refused.
    CHECK(r.out.dfu.blocks_sent == (trailer_off - spare) / MBOOT_DFU_XFER_SIZE + 1);
    CHECK(is_erased(sec->fa_device_id, sec->fa_off + trailer_off, sec->fa_size - trailer_off));

    r = boot();
    CHECK(jumped_to(&r, 1));
    CHECK(r.out.swap_type == BOOT_SWAP_TYPE_NONE);
    free(forged.data);
}

// Layout id checked at boot: a validly signed update with a wrong or missing layout id that reaches the
// secondary slot without the DFU checks (the application writer, a programmer) and is marked
// pending is rejected before boot_go() swaps it in; the slot is cleaned and a good update still
// installs afterwards.
static void test_boot_layout(void) {
    printf("layout id checked at boot\n");
    for (size_t i = 0; i < N_VARIANTS; i++) {
        if (strcmp(variants[i].name, "wrong_layout") != 0 && strcmp(variants[i].name, "no_layout") != 0) {
            continue;
        }
        settled_primary(&v1_initial, 1);
        put_update(&variant_blob[i]);
        run_t p = run_child(act_set_pending);
        CHECK(p.out.rc == 0);
        run_t r = boot();
        if (!CHECK(jumped_to(&r, 1) && r.out.swap_type == BOOT_SWAP_TYPE_NONE)) {
            printf("    %s: %s\n", variants[i].name, describe(&r));
        }
        const struct flash_area *sec = area(FLASH_AREA_IMAGE_SECONDARY(0));
        CHECK(is_erased(0, sec->fa_off, 2 * eu()));
        // No pending update is left: the next boot does nothing.
        r = boot();
        CHECK(jumped_to(&r, 1) && r.ops == 0);
        // A good update is installed afterwards.
        put_update(&v2);
        p = run_child(act_set_pending);
        CHECK(p.out.rc == 0);
        r = boot();
        CHECK(jumped_to(&r, 2) && r.out.swap_type == SWAP_AFTER_UPDATE);
    }
}

static void expect_fsload_refused(uint32_t rest_crc);

#if MBOOT_MIN_IMAGE_SIZE != 0
// Swap using offset only resumes the swap of an image larger than one erase unit, so an update of at
// most one unit is refused by every way that starts one: the manifest of a DFU session, fsload
// (judged as a stream before anything is written) and the check of a pending update at boot, which
// catches an image that reached the slot behind the front ends' back (the application writer, a
// programmer). Nothing outside the update slot changes.
static void test_too_small(void) {
    printf("update of at most one erase unit\n");
    CHECK(small_blob.len <= eu());

    settled_primary(&v1_initial, 1);
    uint32_t pri_crc = crc_area(FLASH_AREA_IMAGE_PRIMARY(0));
    uint32_t rest_crc = crc_flash_except(true);
    run_t r = dfu_session(&small_blob, 0, false);
    const bl_dfu_out_t *d = &r.out.dfu;
    if (CHECK(in_dfu(&r)) && CHECK(d->manifest_status != 0xFF)) {
        CHECK(d->result.code == MBOOT_RES_ERR_TOO_SMALL);
        CHECK(d->manifest_status == DFU_STATUS_ERR_FILE);
        CHECK(d->result.phase == MBOOT_DFU_PHASE_VALIDATE);
        const struct flash_area *sec = area(FLASH_AREA_IMAGE_SECONDARY(0));
        CHECK(is_erased(sec->fa_device_id, sec->fa_off + SPARE, 64));
        CHECK(crc_area(FLASH_AREA_IMAGE_PRIMARY(0)) == pri_crc);
        CHECK(crc_flash_except(true) == rest_crc);
    }
    r = boot();
    CHECK(jumped_to(&r, 1));
    CHECK(r.out.swap_type == BOOT_SWAP_TYPE_NONE);

    settled_primary(&v1_initial, 1);
    rest_crc = crc_flash_except(false);
    CHECK(app_request_fsload("small"));
    expect_fsload_refused(rest_crc);

    settled_primary(&v1_initial, 1);
    put_update(&small_blob);
    run_t p = run_child(act_set_pending);
    CHECK(p.out.rc == 0);
    r = boot();
    CHECK(jumped_to(&r, 1));
    CHECK(r.out.swap_type == BOOT_SWAP_TYPE_NONE);
    const struct flash_area *sec = area(FLASH_AREA_IMAGE_SECONDARY(0));
    CHECK(is_erased(sec->fa_device_id, sec->fa_off + SPARE, 64));
    r = boot();
    CHECK(jumped_to(&r, 1) && r.ops == 0);
    // A good update is installed afterwards.
    put_update(&v2);
    p = run_child(act_set_pending);
    CHECK(p.out.rc == 0);
    r = boot();
    CHECK(jumped_to(&r, 2) && r.out.swap_type == SWAP_AFTER_UPDATE);
}
#else
// The other policies and swap modes take an image of any size.
static void test_small_accepted(void) {
    printf("update of less than one erase unit is taken\n");
    settled_primary(&v1_initial, 1);
    run_t r = dfu_session(&small_blob, 0, false);
    CHECK(r.out.dfu.manifest_status == DFU_STATUS_OK);
    r = boot();
    CHECK(jumped_to(&r, 2));
}
#endif

// Downgrade: an older image is refused by the DFU front end and, when it gets into the secondary
// slot by another way, by bootutil at the next boot.
static void test_downgrade(void) {
    printf("downgrade by %s\n", ROLLBACK_COUNTER ? "security counter" : "version");
    struct {
        const char *name;
        const blob_t *img;
        bool refused;
    } cases[] = {
        {"old_version", &old_version_blob, true},
        // Version 2.0.1 with counter 1: only the counter policy refuses it.
        {"old_counter", &old_counter_blob, ROLLBACK_COUNTER != 0},
    };
    for (size_t i = 0; i < sizeof(cases) / sizeof(cases[0]); i++) {
        settled_primary(&v2_initial, 2);
        uint32_t pri_crc = crc_area(FLASH_AREA_IMAGE_PRIMARY(0));
        run_t r = dfu_session(cases[i].img, 0, false);
        const bl_dfu_out_t *d = &r.out.dfu;
        if (!cases[i].refused) {
            CHECK(d->manifest_status == DFU_STATUS_OK);
            continue;
        }
        if (!CHECK(in_dfu(&r)) || !CHECK(d->manifest_status != 0xFF)) {
            continue;
        }
        if (!CHECK(d->result.code == MBOOT_RES_ERR_DOWNGRADE)) {
            printf("    %s: result code %u\n", cases[i].name, d->result.code);
        }
        CHECK(d->manifest_status == DFU_STATUS_ERR_FILE);
        CHECK(crc_area(FLASH_AREA_IMAGE_PRIMARY(0)) == pri_crc);
        r = boot();
        CHECK(jumped_to(&r, 2));
        CHECK(r.out.swap_type == BOOT_SWAP_TYPE_NONE);

        // The image is put into the update slot behind the front end's back and marked pending:
        // bootutil refuses it at the next boot and erases it.
        put_update(cases[i].img);
        run_t p = run_child(act_set_pending);
        CHECK(p.out.rc == 0);
        r = boot();
        CHECK(jumped_to(&r, 2));
        CHECK(r.out.swap_type == BOOT_SWAP_TYPE_NONE);
        const struct flash_area *sec = area(FLASH_AREA_IMAGE_SECONDARY(0));
        CHECK(is_erased(sec->fa_device_id, sec->fa_off + SPARE, 64));
    }
}

// The checks after a request whose file must be refused: the old image remains bootable and
// no flash writes or erases take place.
static void expect_fsload_refused(uint32_t rest_crc) {
    run_t r = boot();
    CHECK(!r.crash && !r.hang);
    CHECK(r.out.kind == BL_OUT_RESET);
    CHECK(!r.out.dfu.entered);
    CHECK(crc_flash_except(false) == rest_crc);

    r = boot();
    CHECK(jumped_to(&r, 1));
    CHECK(r.out.swap_type == BOOT_SWAP_TYPE_NONE);
}


// Negative images through fsload: the same files from a FAT volume on the SPI flash,
// validated as a stream area before anything is written.
static void test_fsload_rejections(void) {
    printf("negative images through fsload (stream area)\n");
    for (size_t i = 0; i <= N_VARIANTS; i++) {
        const char *name = i < N_VARIANTS ? variants[i].name : "oversize";
        settled_primary(&v1_initial, 1);
        uint32_t rest_crc = crc_flash_except(false);
        CHECK(app_request_fsload(name));
        expect_fsload_refused(rest_crc);
    }
}

#if defined(MBOOT_FSLOAD_RAW)
// The same files from a raw window of the SPI flash.
static void test_fsload_raw_rejections(void) {
    printf("negative images through fsload (raw window)\n");
    for (size_t i = 0; i <= N_VARIANTS; i++) {
        settled_primary(&v1_initial, 1);
        uint32_t rest_crc = crc_flash_except(false);
        CHECK(app_request_fsload_raw(i < N_VARIANTS ? &variant_blob[i] : &oversize_blob));
        expect_fsload_refused(rest_crc);
    }
}

// Windows the bootloader refuses before it reads them: one over a flash area of MCUboot, one
// outside every device, a second window of length 0 is the 10 byte form (a valid request) and a
// second window outside every device.
static void test_fsload_raw_windows(void) {
    printf("raw windows that are refused\n");
    const struct flash_area *pri = area(FLASH_AREA_IMAGE_PRIMARY(0));
    uint32_t pri_addr = mboot_devs[pri->fa_device_id].base + pri->fa_off;
    struct {
        uint32_t base, len, base2, len2;
    } cases[] = {
        {pri_addr, 0x1000, 0, 0},
        {0x70000000u, 0x1000, 0, 0},
        {FILE_BASE, 0x1000, 0x70000000u, 0x1000},
        {FILE_BASE, 0x1000, pri_addr, 0x1000},
    };
    for (size_t i = 0; i < sizeof(cases) / sizeof(cases[0]); i++) {
        settled_primary(&v1_initial, 1);
        uint32_t rest_crc = crc_flash_except(false);
        build_fsload_raw(cases[i].base, cases[i].len, cases[i].base2, cases[i].len2);
        CHECK(request_reset());
        expect_fsload_refused(rest_crc);
    }
}
#endif

#if defined(MBOOT_FSLOAD_GZIP)
// The same files, compressed, in a FAT volume: the reader finds the gzip magic and inflates.
static void test_fsload_gz_rejections(void) {
    printf("negative images through fsload (gzip)\n");
    for (size_t i = 0; i <= N_VARIANTS; i++) {
        const char *name = i < N_VARIANTS ? variants[i].name : "oversize";
        settled_primary(&v1_initial, 1);
        uint32_t rest_crc = crc_flash_except(false);
        CHECK(app_request_fsload_gz(name));
        expect_fsload_refused(rest_crc);
    }
}
#endif

// Good file through the real main flow: the request in the retention RAM loads the file into the
// update slot and marks it pending, then the next boot swaps it in.
static void expect_fsload_installed(void) {
    uint32_t spi_reads = fake_flash_stats()->dev_reads[1];
    run_t r = boot();
    CHECK(!r.crash && !r.hang && r.out.kind == BL_OUT_RESET && !r.out.dfu.entered);
    // The source is read from the SPI flash through the driver and the chip model, and the driver
    // never sends the chip a command it refuses.
    CHECK(fake_flash_stats()->dev_reads[1] > spi_reads);
    CHECK(fake_flash_stats()->violations == 0);
    r = boot();
    CHECK(jumped_to(&r, 2));
    CHECK(r.out.swap_type == SWAP_AFTER_UPDATE);
}

static void test_fsload_good(void) {
    printf("good image through fsload\n");
    settled_primary(&v1_initial, 1);
    CHECK(app_request_fsload("good"));
    expect_fsload_installed();
}

#if defined(MBOOT_FSLOAD_RAW)
static void test_fsload_raw_good(void) {
    printf("good image through fsload (raw window)\n");
    settled_primary(&v1_initial, 1);
    CHECK(app_request_fsload_raw(&good_blob));
    expect_fsload_installed();
}

// The 18 byte MOUNT form: the file is a first window followed by a second one.
static void test_fsload_raw_two_windows(void) {
    printf("good image through fsload (two raw windows)\n");
    settled_primary(&v1_initial, 1);
    uint32_t first = (uint32_t)(good_blob.len / eu() / 2) * eu();
    CHECK(first != 0);
    blob_t head = {good_blob.data, first};
    blob_t tail = {good_blob.data + first, good_blob.len - first};
    uint32_t off2 = file_off() + 0x20000;
    uint32_t len1 = put_raw(&head, file_off());
    uint32_t len2 = put_raw(&tail, off2);
    CHECK(len1 == first);
    build_fsload_raw(FILE_BASE, len1, mboot_devs[1].base + off2, len2);
    CHECK(request_reset());
    expect_fsload_installed();
}
#endif

#if defined(MBOOT_FSLOAD_GZIP)
static void test_fsload_gz_good(void) {
    printf("good image through fsload (gzip)\n");
    settled_primary(&v1_initial, 1);
    CHECK(app_request_fsload_gz("good"));
    expect_fsload_installed();
}
#endif

// A DFU session that is interrupted leaves nothing pending, and a fresh session works.
static void test_dfu_interrupted(void) {
    printf("interrupted DFU session\n");
    settled_primary(&v1_initial, 1);
    uint32_t pri_crc = crc_area(FLASH_AREA_IMAGE_PRIMARY(0));
    unsigned blocks = (unsigned)((good_blob.len + 2047) / 2048);
    unsigned cuts[] = {1, blocks / 2, blocks - 1};
    for (size_t i = 0; i < sizeof(cuts) / sizeof(cuts[0]); i++) {
        run_t r = dfu_session(&good_blob, cuts[i], false);
        CHECK(in_dfu(&r) && r.out.kind == BL_OUT_DFU_WAIT);
        r = boot();
        CHECK(jumped_to(&r, 1));
        CHECK(r.out.swap_type == BOOT_SWAP_TYPE_NONE);
        CHECK(crc_area(FLASH_AREA_IMAGE_PRIMARY(0)) == pri_crc);
    }
    // All blocks but no manifest step: the host went away before the end of the download.
    run_t r = dfu_session(&good_blob, 0, true);
    CHECK(in_dfu(&r) && r.out.kind == BL_OUT_DFU_WAIT);
    r = boot();
    CHECK(jumped_to(&r, 1));
    CHECK(r.out.swap_type == BOOT_SWAP_TYPE_NONE);
    // A fresh complete session succeeds.
    r = dfu_session(&good_blob, 0, false);
    CHECK(r.out.dfu.manifest_status == DFU_STATUS_OK);
    r = boot();
    CHECK(jumped_to(&r, 2));
}

// Boots without a request and lets the host install img when the front end starts.
static run_t boot_with_install(const blob_t *img) {
    bl_shared->script.kind = BL_SCRIPT_INSTALL;
    bl_shared->script.blob = img->data;
    bl_shared->script.len = img->len;
    run_t r = boot();
    memset(&bl_shared->script, 0, sizeof(bl_shared->script));
    return r;
}

// A swap using offset leaves the replaced image at the start of the secondary slot. When the
// primary slot later has no image, bootutil's bootstrap must not take that stale image for an
// update (it copies from one erase unit further in): the primary body must stay as it is, the
// stale header is dropped, and an image installed afterwards boots.
#if defined(MCUBOOT_SWAP_USING_OFFSET)
static void test_stale_secondary(void) {
    printf("no image: stale image at the start of the secondary slot\n");
    settled_primary(&v1_initial, 1);
    put_update(&v2_same_counter);
    run_t p = run_child(act_set_pending);
    CHECK(p.out.rc == 0);
    run_t r = boot();
    CHECK(jumped_to(&r, 2) && r.out.swap_type == BOOT_SWAP_TYPE_REVERT);
    p = run_child(act_confirm);
    CHECK(p.out.rc == 0);
    r = boot();
    CHECK(jumped_to(&r, 2) && r.out.swap_type == BOOT_SWAP_TYPE_NONE);

    const struct flash_area *pri = area(FLASH_AREA_IMAGE_PRIMARY(0));
    const struct flash_area *sec = area(FLASH_AREA_IMAGE_SECONDARY(0));
    CHECK(!is_erased(sec->fa_device_id, sec->fa_off, 16));    // v1 is still there
    fake_flash_erase_raw(pri->fa_device_id, pri->fa_off, eu());
    uint32_t body = crc_range(pri->fa_device_id, pri->fa_off + eu(), 4 * eu());

    r = boot();
    CHECK(in_dfu(&r) && r.out.dfu.why == REC_NO_IMAGE);
    CHECK(crc_range(pri->fa_device_id, pri->fa_off + eu(), 4 * eu()) == body);
    CHECK(is_erased(sec->fa_device_id, sec->fa_off, eu()));
    r = boot();
    CHECK(in_dfu(&r) && r.out.dfu.why == REC_NO_IMAGE);

    r = boot_with_install(&good_blob);
    CHECK(in_dfu(&r) && r.out.dfu.manifest_status == DFU_STATUS_OK);
    r = boot();
    CHECK(jumped_to(&r, 2) && r.out.swap_type == BOOT_SWAP_TYPE_NONE);
}
#endif

// No image: the primary slot holds no image. The bootloader enters DFU without a timeout, DFU
// installs an image and the device boots it.
static void test_no_image(void) {
    printf("no image: erased header\n");
    settled_primary(&v1_initial, 1);
    const struct flash_area *pri = area(FLASH_AREA_IMAGE_PRIMARY(0));
    fake_flash_erase_raw(pri->fa_device_id, pri->fa_off, eu());

    run_t r = boot();
    CHECK(in_dfu(&r) && r.out.kind == BL_OUT_DFU_WAIT);
    CHECK(r.out.dfu.why == REC_NO_IMAGE);
    CHECK(!r.out.dfu.primary.valid);

    r = boot_with_install(&good_blob);
    CHECK(in_dfu(&r) && r.out.dfu.why == REC_NO_IMAGE);
    CHECK(r.out.dfu.manifest_status == DFU_STATUS_OK);
    CHECK(r.out.kind == BL_OUT_RESET);
    // Nothing is marked pending for an empty primary slot: bootutil's bootstrap validates the
    // image, copies it in and confirms it, as there is nothing to revert to. This also works
    // with the version downgrade check, which would compare the image with the erased header.
    r = boot();
    CHECK(jumped_to(&r, 2));
    CHECK(r.out.swap_type == BOOT_SWAP_TYPE_NONE);
    r = boot();
    CHECK(jumped_to(&r, 2));
    CHECK(r.out.swap_type == BOOT_SWAP_TYPE_NONE);
}

// A security counter area that is not erased and holds no valid record is damaged: no image passes
// the rollback check because the counter cannot be read, so DFU is entered. The primary slot is
// not erased: the image is fine, the counter is not.
static void test_seccnt_damaged(void) {
    printf("security counter area damaged: no image runs\n");
    #if ROLLBACK_COUNTER
    settled_primary(&v1_initial, 1);
    uint32_t pri_crc = crc_area(FLASH_AREA_IMAGE_PRIMARY(0));
    const struct flash_area *sc = area(MBOOT_AREA_SECCNT);
    // The records of unit 0 (the initial one and the one the image raised) are overwritten.
    uint8_t *zeros = calloc(1, eu());
    fake_flash_poke(sc->fa_device_id, sc->fa_off, zeros, eu());
    free(zeros);

    run_t r = boot();
    CHECK(in_dfu(&r) && r.out.kind == BL_OUT_DFU_WAIT);
    CHECK(!jumped(&r));
    CHECK(crc_area(FLASH_AREA_IMAGE_PRIMARY(0)) == pri_crc);
    #endif
}

// A primary slot whose image fails the hash and still carries swap state: the bootloader erases
// its trailer and its header sector (and nothing else) before DFU, and an installed image boots.
static void test_damaged_primary(void) {
    printf("damaged primary image: trailer and header erased, nothing else\n");
    settled_primary(&v1_initial, 1);
    const struct flash_area *pri = area(FLASH_AREA_IMAGE_PRIMARY(0));
    uint8_t flipped = fake_flash_data(0)[pri->fa_off + 0x2000] ^ 0x01;
    fake_flash_poke(0, pri->fa_off + 0x2000, &flipped, 1);
    uint32_t trailer_sectors = MBOOT_TRAILER_SECTORS;
    uint32_t trailer_off = pri->fa_off + pri->fa_size - trailer_sectors * eu();
    uint32_t body_crc = crc_range(0, pri->fa_off + eu(), trailer_off - pri->fa_off - eu());

    run_t r = boot();
    CHECK(in_dfu(&r) && r.out.dfu.why == REC_NO_IMAGE);
    // The only slot has no swap state to clean up: the damaged image stays where it is until an
    // install overwrites it.
    CHECK(MBOOT_POLICY_SINGLE || is_erased(0, pri->fa_off, eu()));
    CHECK(MBOOT_POLICY_SINGLE || is_erased(0, trailer_off, trailer_sectors * eu()));
    CHECK(crc_range(0, pri->fa_off + eu(), trailer_off - pri->fa_off - eu()) == body_crc);

    r = boot_with_install(&good_blob);
    CHECK(r.out.dfu.manifest_status == DFU_STATUS_OK);
    r = boot();
    CHECK(jumped_to(&r, 2));
}

// A valid primary image is never touched by the recovery step.
static void act_assert(void) {
    mboot_port_flash_dev_init();
    mboot_assert_fail("bl_test.c", 1);
}

static void test_assert_recovery(void) {
    printf("assert leads to DFU recovery; a valid primary image stays\n");
    settled_primary(&v1_initial, 1);
    uint32_t pri_crc = crc_area(FLASH_AREA_IMAGE_PRIMARY(0));
    bl_shared->script.kind = BL_SCRIPT_NONE;
    run_t r = run_child(act_assert);
    CHECK(in_dfu(&r) && r.out.dfu.why == REC_FAULT);
    CHECK(r.out.dfu.primary.valid && r.out.dfu.primary.ver_major == 1);
    CHECK(crc_area(FLASH_AREA_IMAGE_PRIMARY(0)) == pri_crc);

    // An invalid one loses its trailer and its header, as at a failed boot_go().
    const struct flash_area *pri = area(FLASH_AREA_IMAGE_PRIMARY(0));
    uint8_t flipped = fake_flash_data(0)[pri->fa_off + 0x3000] ^ 0x80;
    fake_flash_poke(0, pri->fa_off + 0x3000, &flipped, 1);
    r = run_child(act_assert);
    CHECK(in_dfu(&r) && r.out.dfu.why == REC_FAULT);
    CHECK(MBOOT_POLICY_SINGLE || is_erased(0, pri->fa_off, eu()));
}

// A recovery request while a test image is unconfirmed. The revert is settled before the
// session, the new image arrives in a clean secondary slot and boots in test mode.
static void test_settle(void) {
    #if MBOOT_POLICY_SWAP
    printf("recovery request with an unconfirmed test image\n");
    #else
    printf("recovery request with a pending update\n");
    #endif
    settled_primary(&v1_initial, 1);
    run_t r = dfu_session(&good_blob, 0, false);
    CHECK(r.out.dfu.manifest_status == DFU_STATUS_OK);
    r = boot();
    CHECK(jumped_to(&r, 2) && r.out.swap_type == SWAP_AFTER_UPDATE);

    // The application running v2 asks for DFU, and a host installs v3.
    r = dfu_session(&v3, 0, false);
    CHECK(in_dfu(&r) && r.out.dfu.why == REC_APP_REQUEST);
    #if MBOOT_POLICY_SWAP
    CHECK(r.out.dfu.primary.valid && r.out.dfu.primary.ver_major == 1);    // v2 was reverted first
    #else
    CHECK(r.out.dfu.primary.valid && r.out.dfu.primary.ver_major == 2);    // v2 is final
    #endif
    CHECK(r.out.dfu.manifest_status == DFU_STATUS_OK);
    CHECK(r.out.kind == BL_OUT_RESET);
    r = boot();
    CHECK(jumped_to(&r, 3));
    CHECK(r.out.swap_type == SWAP_AFTER_UPDATE);    // the new image is in test mode

    // A pending test swap is completed before a forced entry stops at the front end.
    settled_primary(&v1_initial, 1);
    put_update(&good_blob);
    run_t p = run_child(act_set_pending);
    CHECK(p.out.rc == 0);
    bl_shared->entry_forced = true;
    r = boot();
    CHECK(in_dfu(&r) && r.out.dfu.why == REC_FORCED);
    CHECK(r.out.dfu.primary.valid && r.out.dfu.primary.ver_major == 2);
    bl_shared->entry_forced = false;
}

// Hardware entry without a pending swap: boot_go() does not run and the image is untouched.
static void test_forced_entry(void) {
    printf("hardware entry skips boot_go()\n");
    settled_primary(&v1_initial, 1);
    uint32_t all_crc = crc_flash_except(false);
    bl_shared->entry_forced = true;
    run_t r = boot();
    CHECK(in_dfu(&r) && r.out.dfu.why == REC_FORCED);
    CHECK(r.out.dfu.primary.valid && r.out.dfu.primary.ver_major == 1);
    CHECK(crc_flash_except(false) == all_crc);
    bl_shared->entry_forced = false;
    r = boot();
    CHECK(jumped_to(&r, 1));
}

// Three resets from the fault path in a row send the next boot to recovery without boot_go().
static void test_fault_resets(void) {
    printf("repeated fault resets enter recovery without boot_go()\n");
    settled_primary(&v1_initial, 1);
    put_update(&good_blob);
    run_t p = run_child(act_set_pending);
    CHECK(p.out.rc == 0);
    bl_shared->fault_resets = 2;
    run_t r = boot();
    CHECK(jumped_to(&r, 2));    // below the limit the swap runs

    settled_primary(&v1_initial, 1);
    put_update(&good_blob);
    p = run_child(act_set_pending);
    CHECK(p.out.rc == 0);
    bl_shared->fault_resets = 3;
    r = boot();
    CHECK(in_dfu(&r) && r.out.dfu.why == REC_FAULT);
    CHECK(r.out.dfu.primary.valid && r.out.dfu.primary.ver_major == 1);    // the swap did not run
}

// A flash map that does not match the device ends in DFU, not in a halt.
static void test_map_check_failure(void) {
    printf("flash map check failure enters recovery\n");
    flash_fresh();
    flash_devices(mboot_devs[0].size / 2);
    run_t r = boot();
    CHECK(in_dfu(&r) && r.out.dfu.why == REC_FAULT);
}

// mboot_validate_view() on the registered areas and on a failing read.
static void test_validate_direct(void) {
    printf("mboot_validate_view on registered areas, flash errors\n");
    flash_fresh();
    put_primary(&v1_initial);
    put_update(&good_blob);
    const struct flash_area *pri = area(FLASH_AREA_IMAGE_PRIMARY(0));
    const struct flash_area *sec = area(FLASH_AREA_IMAGE_SECONDARY(0));

    mboot_validate_result_t r = mboot_validate_view(pri, VALIDATE_FULL | VALIDATE_CHECK_TARGET);
    CHECK(r.code == MBOOT_RES_OK && r.info.valid && r.info.ver_major == 1 && r.info.sec_cnt == 1);
    r = mboot_validate_view(sec, VALIDATE_FULL | VALIDATE_CHECK_TARGET | VALIDATE_CHECK_DOWNGRADE);
    CHECK(r.code == MBOOT_RES_OK && r.info.ver_major == 2 && r.info.sec_cnt == 2 && r.info.hash_prefix != 0);

    mboot_image_info_t info;
    mboot_image_info_read(sec, &info);
    CHECK(info.valid && info.ver_major == 2 && info.hash_prefix == r.info.hash_prefix);

    // A body unit of the update that reads as an ECC error: a flash error at that offset. A SPI
    // flash has no ECC, its reads do not fail.
    if (sec->fa_device_id == 0) {
        uint8_t other[16];
        memset(other, 0x5A, sizeof(other));
        uint32_t bad = 0x1000;
        mboot_port_flash_dev_write(0, sec->fa_off + SPARE + bad, other, sizeof(other));    // programmed twice
        r = mboot_validate_view(sec, VALIDATE_FULL);
        CHECK(r.code == MBOOT_RES_ERR_FLASH);
        CHECK(r.detail == bad);
    }
}

// The application image built by ports/stm32 (MBOOT_BACKEND=mcuboot) for the board whose mboot_layout.mk this
// harness was built from: installed over DFU into an empty device, it passes the checks of the
// bootloader (layout id, signature, counter) and runs.
static const char *app_image;

static void test_real_app(void) {
    printf("real application image into an empty device\n");
    if (app_image == NULL) {
        printf("  (no --app image given)\n");
        return;
    }
    char dir[512];
    char name[256];
    const char *slash = strrchr(app_image, '/');
    snprintf(dir, sizeof(dir), "%.*s", slash != NULL ? (int)(slash - app_image) : 1, slash != NULL ? app_image : ".");
    snprintf(name, sizeof(name), "%s", slash != NULL ? slash + 1 : app_image);
    blob_t app = load_blob(dir, name);

    flash_fresh();
    run_t r = boot();
    CHECK(in_dfu(&r) && r.out.dfu.why == REC_NO_IMAGE);
    r = boot_with_install(&app);
    CHECK(in_dfu(&r));
    CHECK(r.out.dfu.manifest_status == DFU_STATUS_OK);
    if (r.out.dfu.manifest_status != DFU_STATUS_OK) {
        printf("    manifest status %02x result code %u\n", r.out.dfu.manifest_status, r.out.dfu.result.code);
    }
    r = boot();
    CHECK(jumped(&r) && r.out.running.ver_major == app.data[20] && r.out.swap_type == BOOT_SWAP_TYPE_NONE);
    printf("  installed %zu bytes, then: %s\n", app.len, describe(&r));
}


// ---- the single slot policy ----
//
// The only slot is written in place. There is no pending state, no revert and no old image to
// fall back to: an update that does not complete or does not validate leaves a slot that boot_go()
// refuses to start, and the bootloader enters the DFU front end (fail closed). A DFU install
// brings the device back.

#if MBOOT_POLICY_SINGLE

static void expect_no_image(void) {
    run_t r = boot();
    CHECK(in_dfu(&r) && r.out.kind == BL_OUT_DFU_WAIT);
    CHECK(r.out.dfu.why == REC_NO_IMAGE);
    CHECK(!jumped(&r));
}

static void expect_dfu_recovers(void) {
    run_t r = boot_with_install(&v3);
    CHECK(in_dfu(&r) && r.out.dfu.manifest_status == DFU_STATUS_OK);
    r = boot();
    CHECK(jumped_to(&r, 3) && r.out.swap_type == BOOT_SWAP_TYPE_NONE);
}

static void test_single_install(void) {
    printf("single slot: image over DFU, written in place\n");
    settled_primary(&v1_initial, 1);
    run_t r = dfu_session(&good_blob, 0, false);
    CHECK(in_dfu(&r) && r.out.dfu.why == REC_APP_REQUEST);
    CHECK(r.out.dfu.manifest_status == DFU_STATUS_OK);
    CHECK(r.out.dfu.result.code == MBOOT_RES_OK && r.out.dfu.result.phase == MBOOT_DFU_PHASE_VALIDATE);
    r = boot();
    CHECK(jumped_to(&r, 2));
    CHECK(r.out.swap_type == BOOT_SWAP_TYPE_NONE);
    r = boot();
    CHECK(jumped_to(&r, 2));
    CHECK(fake_flash_stats()->violations == 0);
}

// A rejected image has overwritten the only slot: nothing runs and DFU is entered.
static void test_single_rejections(void) {
    printf("single slot: negative images over DFU leave no image\n");
    for (size_t i = 0; i < N_VARIANTS; i++) {
        settled_primary(&v1_initial, 1);
        run_t r = dfu_session(&variant_blob[i], 0, false);
        const bl_dfu_out_t *d = &r.out.dfu;
        if (!CHECK(in_dfu(&r)) || !CHECK(d->manifest_status != 0xFF)) {
            printf("    %s: %s\n", variants[i].name, describe(&r));
            continue;
        }
        // The sectors a truncated file does not reach still hold the old image, so the hash of
        // the slot is what fails there.
        bool truncated = strncmp(variants[i].name, "truncated", 9) == 0;
        if (!CHECK(code_in(d->result.code, variants[i].codes) || (truncated && d->result.code == MBOOT_RES_ERR_HASH))) {
            printf("    %s: result code %u\n", variants[i].name, d->result.code);
        }
        CHECK(d->manifest_status == dfu_status_for(d->result.code));
        // Wrong layout id: the signature is good, so boot_go() alone would start it. The front end
        // erased the header of the rejected image.
        expect_no_image();
        expect_dfu_recovers();
    }
}

static void test_single_oversize(void) {
    printf("single slot: oversize image over DFU\n");
    settled_primary(&v1_initial, 1);
    run_t r = dfu_session(&oversize_blob, 0, false);
    CHECK(in_dfu(&r));
    CHECK(r.out.dfu.block_status == DFU_STATUS_ERR_ADDRESS);
    CHECK(r.out.dfu.manifest_status == 0xFF);
    // The blocks up to the end of the image area overwrote the slot.
    expect_no_image();
    expect_dfu_recovers();
}

// The only slot holds no swap state, so the DFU region is all of it: a download of a slot sized
// image is accepted to its last block, and an image that uses the slot up to the reservation of
// MBOOT_TRAILER_SIZE bytes at its end boots.
static void test_single_region(void) {
    printf("single slot: the DFU region is the whole slot\n");
    settled_primary(&v1_initial, 1);
    const struct flash_area *pri = area(FLASH_AREA_IMAGE_PRIMARY(0));
    uint32_t trailer_off;
    CHECK(mboot_flash_slot_trailer_off(pri, &trailer_off) == 0);
    CHECK(trailer_off == pri->fa_size);
    blob_t big = {malloc(pri->fa_size), pri->fa_size};
    CHECK(big.data != NULL);
    memset(big.data, 0xFF, big.len);
    memcpy(big.data, v2.data, v2.len);
    run_t r = dfu_session(&big, 0, false);
    CHECK(r.out.dfu.block_status == DFU_STATUS_OK);
    CHECK(r.out.dfu.blocks_sent == (pri->fa_size + MBOOT_DFU_XFER_SIZE - 1) / MBOOT_DFU_XFER_SIZE);
    CHECK(r.out.dfu.manifest_status == DFU_STATUS_OK);
    r = boot();
    CHECK(jumped_to(&r, 2));
    free(big.data);
}

static void test_single_interrupted(void) {
    printf("single slot: interrupted DFU session\n");
    unsigned blocks = (unsigned)((good_blob.len + 2047) / 2048);
    unsigned cuts[] = {1, blocks / 2, blocks - 1};
    for (size_t i = 0; i < sizeof(cuts) / sizeof(cuts[0]); i++) {
        settled_primary(&v1_initial, 1);
        run_t r = dfu_session(&good_blob, cuts[i], false);
        CHECK(in_dfu(&r) && r.out.kind == BL_OUT_DFU_WAIT);
        expect_no_image();
        expect_dfu_recovers();
    }
    // All blocks but no manifest step: the image is complete and validates, nothing else is needed.
    settled_primary(&v1_initial, 1);
    run_t r = dfu_session(&good_blob, 0, true);
    CHECK(in_dfu(&r) && r.out.kind == BL_OUT_DFU_WAIT);
    r = boot();
    CHECK(jumped_to(&r, 2));
}

// The security counter of the running image is raised at the boot, so that an older image written
// to the only slot later is refused by the front end and by boot_go().
static void test_single_downgrade(void) {
    printf("single slot: downgrade refused by the security counter\n");
    settled_primary(&v2_initial, 2);
    run_t r = dfu_session(&old_counter_blob, 0, false);
    CHECK(in_dfu(&r) && r.out.dfu.manifest_status != 0xFF);
    CHECK(r.out.dfu.result.code == MBOOT_RES_ERR_DOWNGRADE);
    expect_no_image();
    expect_dfu_recovers();

    // The older image gets into the slot another way (a programmer): boot_go() refuses it.
    settled_primary(&v2_initial, 2);
    const struct flash_area *pri = area(FLASH_AREA_IMAGE_PRIMARY(0));
    fake_flash_erase_raw(pri->fa_device_id, pri->fa_off, pri->fa_size);
    put_primary(&v1_initial);
    r = boot();
    CHECK(in_dfu(&r) && r.out.dfu.why == REC_NO_IMAGE);
}

// An image that is installed from a file: the request is saved in the intent area first and the
// area is erased when the install is done.
static void test_single_fsload(void) {
    printf("single slot: image through fsload, intent area\n");
    settled_primary(&v1_initial, 1);
    const struct flash_area *intent = area(MBOOT_AREA_INTENT);
    CHECK(intent != NULL && is_erased(intent->fa_device_id, intent->fa_off, intent->fa_size));
    CHECK(app_request_fsload("good"));
    expect_fsload_installed();
    CHECK(is_erased(intent->fa_device_id, intent->fa_off, intent->fa_size));
    run_t r = boot();
    CHECK(jumped_to(&r, 2));
}

#endif

// ---- power cut sweeps through the real main flow ----
//
// A scenario starts from a flash state and runs the boot that does the work (a swap, a revert, a
// DFU session, the recovery of an empty slot). The sweep first runs it once to count and label
// the flash operations, then repeats it from the same state with a power cut at every operation
// (before it, after it, between two units of it and torn inside it at three points), lets the
// device boot until it settles and judges the outcome. A device that ends in DFU although the
// scenario did not expect it has lost its image; the DFU host then installs another image to see
// that recovery works. Nothing is ever confirmed by the application.

typedef struct scn scn_t;
struct scn {
    const char *name;
    const blob_t *v1;               // image of the primary slot (padded)
    const blob_t *v2;               // update image
    const blob_t *v3;               // image the DFU host installs when the device lost its image
    void (*setup)(const scn_t *s);
    bool script_in_trial;           // the DFU host installs v2 in the boot that is cut
    bool dfu_expected;              // DFU is part of the scenario (empty primary slot)
    bool lost_ok;                   // an interrupted update may leave no image (single slot policy: fail closed)
    uint8_t final_major;            // image that runs in the end when nothing was lost; 0: the old or the new one
};

// An update that nobody confirms ends on the old image with the swap policy (it reverts); the
// other policies keep the new image once it has been installed, and a cut before that keeps the old one.
#define FINAL_UPDATE (MBOOT_POLICY_SWAP ? 1 : 0)

static void setup_swap(const scn_t *s) {
    settled_primary(s->v1, 1);
    put_update(s->v2);
    run_t p = run_child(act_set_pending);
    CHECK(p.out.rc == 0);
}

#if MBOOT_POLICY_SWAP
static void setup_revert(const scn_t *s) {
    setup_swap(s);
    run_t r = boot();
    CHECK(jumped_to(&r, 2) && r.out.swap_type == BOOT_SWAP_TYPE_REVERT);
}
#endif

static void setup_dfu(const scn_t *s) {
    settled_primary(s->v1, 1);
    CHECK(app_request_dfu());
}

#if MBOOT_POLICY_SINGLE
// The request to load /update.bin from the FAT volume is in the retention RAM.
static void setup_fsload(const scn_t *s) {
    settled_primary(s->v1, 1);
    CHECK(app_request_fsload("good"));
}
#endif

// The primary image was replaced by an update that stayed (confirmed), then its header is lost:
// the replaced image is still at the start of the secondary slot.
#if defined(MCUBOOT_SWAP_USING_OFFSET)
static void setup_stale(const scn_t *s) {
    settled_primary(s->v1, 1);
    put_update(&v2_same_counter);
    run_t p = run_child(act_set_pending);
    CHECK(p.out.rc == 0);
    run_t r = boot();
    CHECK(jumped_to(&r, 2));
    p = run_child(act_confirm);
    CHECK(p.out.rc == 0);
    r = boot();
    CHECK(jumped_to(&r, 2) && r.out.swap_type == BOOT_SWAP_TYPE_NONE);
    const struct flash_area *pri = area(FLASH_AREA_IMAGE_PRIMARY(0));
    fake_flash_erase_raw(pri->fa_device_id, pri->fa_off, eu());
}
#endif

static void setup_empty(const scn_t *s) {
    settled_primary(s->v1, 1);
    const struct flash_area *pri = area(FLASH_AREA_IMAGE_PRIMARY(0));
    fake_flash_erase_raw(pri->fa_device_id, pri->fa_off, eu());
}

static void setup_damaged(const scn_t *s) {
    settled_primary(s->v1, 1);
    const struct flash_area *pri = area(FLASH_AREA_IMAGE_PRIMARY(0));
    uint8_t flipped = fake_flash_data(0)[pri->fa_off + 0x2000] ^ 0x01;
    fake_flash_poke(0, pri->fa_off + 0x2000, &flipped, 1);
}

static uint16_t rd16(const uint8_t *p) {
    return (uint16_t)(p[0] | p[1] << 8);
}

static uint32_t rd32(const uint8_t *p) {
    return p[0] | (uint32_t)p[1] << 8 | (uint32_t)p[2] << 16 | (uint32_t)p[3] << 24;
}

// Bytes of an image: header, body, protected TLVs and the unprotected TLV area (each TLV area
// has its length, including its 4 byte info, in the info).
static size_t image_length(const blob_t *img) {
    size_t unprot = (size_t)rd16(img->data + 8) + rd32(img->data + 12) + rd16(img->data + 10);
    return unprot + rd16(img->data + unprot + 2);
}

typedef struct {
    bool halt, lost, wrong, no_conv, bad_ident, no_dfu, assert_seen;
    bool lost_ok;       // a lost image is the specified outcome of the scenario (single slot policy)
} verdict_t;

static bool log_has_assert(void) {
    return memmem(bl_shared->log_tail, bl_shared->log_tail_len, "ASSERT", 6) != NULL;
}

static void clear_log_tail(void) {
    bl_shared->log_tail_len = 0;
}

static void set_install_script(const blob_t *img) {
    bl_shared->script.kind = BL_SCRIPT_INSTALL;
    bl_shared->script.blob = img->data;
    bl_shared->script.len = img->len;
    bl_shared->script.max_blocks = 0;
    bl_shared->script.omit_manifest = false;
}

// Boots until the device is stable (a boot that jumps without any flash operation and without a
// pending revert) and judges the state. first is the boot that was cut.
static verdict_t settle(const scn_t *s, const run_t *first) {
    verdict_t v;
    memset(&v, 0, sizeof(v));
    v.lost_ok = s->lost_ok;
    const blob_t *settle_img = s->dfu_expected ? s->v2 : s->v3;
    uint8_t expect = s->final_major;
    bool either = expect == 0;
    bool stable = false;
    int final_major = -1;

    if (first->hang || first->crash) {
        v.halt = true;
        return v;
    }
    v.assert_seen = log_has_assert();
    for (int i = 0; i < 10 && !stable; i++) {
        set_install_script(settle_img);
        clear_log_tail();
        run_t r = boot();
        memset(&bl_shared->script, 0, sizeof(bl_shared->script));
        if (r.hang || r.crash) {
            v.halt = true;
            return v;
        }
        v.assert_seen = v.assert_seen || log_has_assert();
        if (in_dfu(&r) && !s->dfu_expected) {
            v.lost = true;
            expect = 3;
        }
        if (jumped(&r)) {
            final_major = r.out.running.ver_major;
            stable = r.ops == 0 && r.out.swap_type == BOOT_SWAP_TYPE_NONE;
        }
    }
    if (either && !v.lost && (final_major == 1 || final_major == 2)) {
        expect = (uint8_t)final_major;
    }
    if (first->out.dfu.entered && !s->dfu_expected && !s->script_in_trial) {
        v.lost = true;
        expect = 3;
    }
    if (!stable) {
        v.no_conv = true;
        return v;
    }
    if (final_major != expect) {
        if (v.lost) {
            v.no_dfu = true;
        } else {
            v.wrong = true;
        }
        return v;
    }
    const blob_t *want = expect == 1 ? s->v1 : expect == 2 ? s->v2 : s->v3;
    const struct flash_area *pri = area(FLASH_AREA_IMAGE_PRIMARY(0));
    if (memcmp(fake_flash_data(0) + pri->fa_off, want->data, image_length(want)) != 0) {
        v.bad_ident = true;
    }
    return v;
}

typedef struct {
    unsigned runs, ok, lost, wrong, halts, no_conv, bad_ident, no_dfu, asserts;
} tally_t;

static void tally_add(tally_t *t, const verdict_t *v) {
    t->runs++;
    bool failed = v->halt || v->wrong || v->no_conv || v->bad_ident || v->no_dfu || (v->lost && !v->lost_ok);
    t->ok += !failed;
    t->lost += v->lost;
    t->wrong += v->wrong;
    t->halts += v->halt;
    t->no_conv += v->no_conv;
    t->bad_ident += v->bad_ident;
    t->no_dfu += v->no_dfu;
    t->asserts += v->assert_seen;
}

static bool tally_failed(const tally_t *t) {
    return t->ok != t->runs;
}

typedef enum {
    CL_PRI_BODY, CL_PRI_TRAILER, CL_SEC_SPARE, CL_SEC_BODY, CL_SEC_TRAILER, CL_SHADOW, CL_SECCNT, CL_OTHER, CL_COUNT
} op_class_t;

static const char *const class_name[CL_COUNT] = {
    "primary body", "primary trailer", "secondary spare", "secondary body", "secondary trailer", "shadow", "seccnt", "other"
};

static bool in_area(const fake_flash_op_t *op, const struct flash_area *fa) {
    return op->dev == fa->fa_device_id && op->off >= fa->fa_off && op->off < fa->fa_off + fa->fa_size;
}

static op_class_t classify(const fake_flash_op_t *op) {
    const struct flash_area *pri = area(FLASH_AREA_IMAGE_PRIMARY(0));
    const struct flash_area *sec = area(FLASH_AREA_IMAGE_SECONDARY(0));
    // The only slot of the single policy has no trailer sectors.
    uint32_t trailer = MBOOT_POLICY_SINGLE ? 0 : MBOOT_TRAILER_SECTORS *eu();
    if (in_area(op, pri)) {
        return op->off >= pri->fa_off + pri->fa_size - trailer ? CL_PRI_TRAILER : CL_PRI_BODY;
    }
    if (in_area(op, sec)) {
        if (op->off < sec->fa_off + SPARE) {
            return CL_SEC_SPARE;
        }
        return op->off >= sec->fa_off + sec->fa_size - trailer ? CL_SEC_TRAILER : CL_SEC_BODY;
    }
    #if defined(MBOOT_ECC_SHADOW)
    if (in_area(op, area(MBOOT_AREA_SHADOW))) {
        return CL_SHADOW;
    }
    #endif
    #if ROLLBACK_COUNTER
    if (in_area(op, area(MBOOT_AREA_SECCNT))) {
        return CL_SECCNT;
    }
    #endif
    return CL_OTHER;
}

static const struct {
    fake_flash_cut_mode_t mode;
    uint32_t q8;
    bool torn;
    const char *name;
} cut_variants[] = {
    {FAKE_FLASH_CUT_BEFORE, 0, false, "before"},
    {FAKE_FLASH_CUT_AFTER, 0, false, "after"},
    {FAKE_FLASH_CUT_BETWEEN, 128, false, "between"},
    {FAKE_FLASH_CUT_BETWEEN, 255, false, "between, last unit left"},
    {FAKE_FLASH_CUT_DURING, 64, true, "torn 1/4"},
    {FAKE_FLASH_CUT_DURING, 128, true, "torn 1/2"},
    {FAKE_FLASH_CUT_DURING, 192, true, "torn 3/4"},
    {FAKE_FLASH_CUT_DURING, 0, true, "torn first unit"},
    {FAKE_FLASH_CUT_DURING, 255, true, "torn last unit"},
    {FAKE_FLASH_CUT_WEAK, 0, true, "corrected first unit (data)"},
    {FAKE_FLASH_CUT_WEAK, 0, true, "corrected first unit (erased)"},
};
#define N_CUT_VARIANTS (sizeof(cut_variants) / sizeof(cut_variants[0]))

static unsigned sweep_failures_printed;

// --only SCENARIO:K:VARIANT runs one cut of one sweep scenario with the boot log echoed
// (SCENARIO is a prefix of the scenario name, VARIANT an index into cut_variants).
static const char *only_scn;
static unsigned only_k;
static unsigned only_variant;

// --reps N tries N random bit patterns for a torn unit at each cut point, --scn PREFIX runs the
// sweep scenarios whose name starts with PREFIX.
static unsigned sweep_reps = 1;
static const char *sweep_filter;

static void sweep_scenario(const scn_t *s, unsigned stride) {
    if (only_scn != NULL && strncmp(s->name, only_scn, strlen(only_scn)) != 0) {
        return;
    }
    if (sweep_filter != NULL && strncmp(s->name, sweep_filter, strlen(sweep_filter)) != 0) {
        return;
    }
    s->setup(s);
    fake_flash_snapshot_t *snap = fake_flash_snapshot();
    bl_shared_t saved = *bl_shared;

    // Baseline: the boot that does the work, uncut.
    if (s->script_in_trial) {
        set_install_script(s->v2);
    }
    clear_log_tail();
    fake_flash_counter_reset();
    run_t base = boot();
    memset(&bl_shared->script, 0, sizeof(bl_shared->script));
    uint32_t n_ops = fake_flash_op_count();
    fake_flash_op_t *ops = calloc(n_ops ? n_ops : 1, sizeof(*ops));
    if (ops == NULL || fake_flash_trace_count() < n_ops) {
        printf("  FAIL no trace\n");
        n_failures++;
        free(ops);
        return;
    }
    memcpy(ops, fake_flash_trace(), n_ops * sizeof(*ops));
    CHECK(!base.hang && !base.crash);

    tally_t total[2];
    tally_t per_class[CL_COUNT];
    unsigned class_ops[CL_COUNT];
    memset(total, 0, sizeof(total));
    memset(per_class, 0, sizeof(per_class));
    memset(class_ops, 0, sizeof(class_ops));

    for (uint32_t k = 1; k <= n_ops; k++) {
        op_class_t cl = classify(&ops[k - 1]);
        class_ops[cl]++;
        if (stride > 1 && k % stride != 0 && k != 1 && k != n_ops) {
            continue;
        }
        if (only_scn != NULL && k != only_k) {
            continue;
        }
        if (only_scn != NULL) {
            printf("--- op %u: %s dev %u offset 0x%x length 0x%x (%s)\n", (unsigned)k, ops[k - 1].kind == FAKE_FLASH_OP_WRITE ? "write" : "erase",
                (unsigned)ops[k - 1].dev, (unsigned)ops[k - 1].off, (unsigned)ops[k - 1].len, class_name[cl]);
        }
        for (size_t c = 0; c < N_CUT_VARIANTS; c++) {
            if (only_scn != NULL && c != only_variant) {
                continue;
            }
            // The random bits of a torn unit differ per rep.
            unsigned nrep = cut_variants[c].mode == FAKE_FLASH_CUT_DURING && only_scn == NULL ? sweep_reps : 1;
            for (unsigned rep = 0; rep < nrep; rep++) {
                fake_flash_restore(snap);
                *bl_shared = saved;
                if (s->script_in_trial) {
                    set_install_script(s->v2);
                }
                clear_log_tail();
                // A corrected unit reads erased for an odd seed, so the two variants differ in the parity.
                uint32_t seed = cut_variants[c].mode == FAKE_FLASH_CUT_WEAK ? ((k * 7) & ~1u) | ((uint32_t)c & 1u) : k * 7 + (uint32_t)c;
                seed += rep * 0x9e3779b9u;
                fake_flash_arm(k, cut_variants[c].mode, cut_variants[c].q8, seed);
                run_t r = boot();
                fake_flash_disarm();
                memset(&bl_shared->script, 0, sizeof(bl_shared->script));
                verdict_t v = settle(s, &r);
                tally_add(&total[cut_variants[c].torn], &v);
                tally_add(&per_class[cl], &v);
                bool failed = v.halt || v.wrong || v.no_conv || v.bad_ident || v.no_dfu || v.lost;
                if (failed && sweep_failures_printed < 12) {
                    sweep_failures_printed++;
                    printf("    %s op %u (%s, %s): %s%s%s%s%s%s%s\n", s->name, (unsigned)k, class_name[cl], cut_variants[c].name,
                        v.halt ? "halt " : "", v.lost ? "lost image " : "", v.wrong ? "wrong final image (lost revert) " : "",
                        v.no_conv ? "not converged " : "", v.bad_ident ? "image bytes differ " : "", v.no_dfu ? "no DFU recovery " : "",
                        v.assert_seen ? "(assert seen)" : "");
                }
            }
        }
    }
    fake_flash_restore(snap);
    fake_flash_snapshot_free(snap);
    free(ops);

    printf("  %-14s %4u operations: clean %u runs ok %u, torn %u runs ok %u; halts %u lost %u wrong %u no_conv %u bad_ident %u no_dfu %u asserts %u\n",
        s->name, n_ops, total[0].runs, total[0].ok, total[1].runs, total[1].ok, total[0].halts + total[1].halts, total[0].lost + total[1].lost,
        total[0].wrong + total[1].wrong, total[0].no_conv + total[1].no_conv, total[0].bad_ident + total[1].bad_ident,
        total[0].no_dfu + total[1].no_dfu, total[0].asserts + total[1].asserts);
    for (int i = 0; i < CL_COUNT; i++) {
        if (class_ops[i] != 0) {
            printf("    %-18s %4u operations, %4u cut runs, %u failed\n", class_name[i], class_ops[i], per_class[i].runs,
                per_class[i].runs - per_class[i].ok);
        }
    }
    for (int t = 0; t < 2; t++) {
        if (tally_failed(&total[t])) {
            n_failures += (int)(total[t].runs - total[t].ok);
        }
    }
    n_checks += (int)(total[0].runs + total[1].runs);
}

static blob_t big_v1, big_v2, big_v3;
static unsigned sweep_stride = 1;
static bool sweep_big;

static void test_sweeps(void) {
    printf("swap, revert and recovery power cut sweeps through mboot_main (stride %u)\n", sweep_stride);
    blob_t *wrong_layout = NULL;
    for (size_t i = 0; i < N_VARIANTS; i++) {
        if (strcmp(variants[i].name, "wrong_layout") == 0) {
            wrong_layout = &variant_blob[i];
        }
    }
    (void)wrong_layout;
    const scn_t small[] = {
        #if !MBOOT_POLICY_SINGLE
        {"swap", &v1_initial, &v2, &v3, setup_swap, false, false, false, FINAL_UPDATE},
        {"reject", &v1_initial, wrong_layout, &v3, setup_swap, false, false, false, 1},
        #endif
        #if MBOOT_POLICY_SWAP
        {"revert", &v1_initial, &v2, &v3, setup_revert, false, false, false, 1},
        #endif
        // The single slot policy loses the image when an update is cut (fail closed) and gets it
        // back from DFU; an update from a file is restarted from the intent area.
        {"DFU session", &v1_initial, &good_blob, &v3, setup_dfu, true, false, MBOOT_POLICY_SINGLE, FINAL_UPDATE},
        #if MBOOT_POLICY_SINGLE
        {"fsload", &v1_initial, &good_blob, &v3, setup_fsload, false, false, false, FINAL_UPDATE},
        #endif
        {"no image empty", &v1_initial, &good_blob, &v3, setup_empty, true, true, false, 2},
        {"no image damaged", &v1_initial, &good_blob, &v3, setup_damaged, true, true, false, 2},
        #if defined(MCUBOOT_SWAP_USING_OFFSET)
        {"no image stale", &v1_initial, &v2_same_counter, &v3, setup_stale, true, true, false, 2},
        #endif
    };
    for (size_t i = 0; i < sizeof(small) / sizeof(small[0]); i++) {
        sweep_scenario(&small[i], sweep_stride);
    }
    if (sweep_big) {
        const scn_t big[] = {
            #if !MBOOT_POLICY_SINGLE
            {"swap 15s", &big_v1, &big_v2, &big_v3, setup_swap, false, false, false, FINAL_UPDATE},
            #endif
            #if MBOOT_POLICY_SWAP
            {"revert 15s", &big_v1, &big_v2, &big_v3, setup_revert, false, false, false, 1},
            #endif
            #if MBOOT_POLICY_SINGLE
            {"DFU session 15s", &big_v1, &big_v2, &big_v3, setup_dfu, true, false, true, FINAL_UPDATE},
            #endif
        };
        for (size_t i = 0; i < sizeof(big) / sizeof(big[0]); i++) {
            sweep_scenario(&big[i], sweep_stride);
        }
    }
}

// ---- main ----

typedef struct {
    const char *name;
    void (*fn)(void);
} test_t;

static const test_t tests[] = {
    #if MBOOT_POLICY_SINGLE
    {"good", test_single_install},
    {"dfu", test_single_rejections},
    {"oversize", test_single_oversize},
    {"region", test_single_region},
    {"downgrade", test_single_downgrade},
    {"interrupted", test_single_interrupted},
    {"single_fsload", test_single_fsload},
    #else
    {"good", test_good_install},
    {"dfu", test_dfu_rejections},
    {"oversize", test_dfu_oversize},
    {"trailer_forged", test_dfu_trailer_forged},
    {"boot_layout", test_boot_layout},
    {"downgrade", test_downgrade},
    #endif
    #if MBOOT_MIN_IMAGE_SIZE != 0
    {"too_small", test_too_small},
    #else
    {"small_accepted", test_small_accepted},
    #endif
    {"fsload", test_fsload_rejections},
    {"fsload_good", test_fsload_good},
    #if defined(MBOOT_FSLOAD_RAW)
    {"fsload_raw", test_fsload_raw_rejections},
    {"fsload_raw_windows", test_fsload_raw_windows},
    {"fsload_raw_good", test_fsload_raw_good},
    {"fsload_raw_two", test_fsload_raw_two_windows},
    #endif
    #if defined(MBOOT_FSLOAD_GZIP)
    {"fsload_gz", test_fsload_gz_rejections},
    {"fsload_gz_good", test_fsload_gz_good},
    #endif
    #if !MBOOT_POLICY_SINGLE
    {"interrupted", test_dfu_interrupted},
    #endif
    {"no_image", test_no_image},
    {"seccnt_damaged", test_seccnt_damaged},
    #if defined(MCUBOOT_SWAP_USING_OFFSET)
    {"stale_secondary", test_stale_secondary},
    #endif
    {"damaged", test_damaged_primary},
    {"assert", test_assert_recovery},
    #if !MBOOT_POLICY_SINGLE
    {"settle", test_settle},
    #endif
    {"forced", test_forced_entry},
    #if !MBOOT_POLICY_SINGLE
    {"faults", test_fault_resets},
    #endif
    {"map_check", test_map_check_failure},
    #if !MBOOT_POLICY_SINGLE
    {"validate", test_validate_direct},
    #endif
    {"sweep", test_sweeps},
    {"real_app", test_real_app},
};

int main(int argc, char **argv) {
    const char *only = NULL;
    for (int i = 1; i < argc; i++) {
        if (strcmp(argv[i], "--images") == 0 && i + 1 < argc) {
            img_dir = argv[++i];
        } else if (strcmp(argv[i], "--echo") == 0) {
            echo_log = true;
        } else if (strcmp(argv[i], "-v") == 0) {
            verbose = true;
        } else if (strcmp(argv[i], "--only") == 0 && i + 1 < argc) {
            static char spec[128];
            snprintf(spec, sizeof(spec), "%s", argv[++i]);
            char *k = strchr(spec, ':');
            char *c = k != NULL ? strchr(k + 1, ':') : NULL;
            if (k == NULL || c == NULL) {
                fprintf(stderr, "--only SCENARIO:K:VARIANT\n");
                return 2;
            }
            *k = *c = '\0';
            only_scn = spec;
            only_k = (unsigned)atoi(k + 1);
            only_variant = (unsigned)atoi(c + 1);
            echo_log = true;
        } else if (strcmp(argv[i], "--stride") == 0 && i + 1 < argc) {
            sweep_stride = (unsigned)atoi(argv[++i]);
        } else if (strcmp(argv[i], "--app") == 0 && i + 1 < argc) {
            app_image = argv[++i];
        } else if (strcmp(argv[i], "--big") == 0) {
            sweep_big = true;
        } else if (strcmp(argv[i], "--reps") == 0 && i + 1 < argc) {
            sweep_reps = (unsigned)atoi(argv[++i]);
        } else if (strcmp(argv[i], "--scn") == 0 && i + 1 < argc) {
            sweep_filter = argv[++i];
        } else if (strcmp(argv[i], "--nor-scatter") == 0) {
            nor_scatter = true;
        } else {
            only = argv[i];
        }
    }
    bl_port_init();
    load_images();
    if (sweep_big) {
        char dir[512];
        snprintf(dir, sizeof(dir), "%s/big", img_dir);
        big_v1 = load_blob(dir, "v1_initial.bin");
        big_v2 = load_blob(dir, "v2.bin");
        big_v3 = load_blob(dir, "v3.bin");
    }
    int ran = 0;
    for (size_t i = 0; i < sizeof(tests) / sizeof(tests[0]); i++) {
        bool sweep = tests[i].fn == test_sweeps || tests[i].fn == test_real_app;
        if (only == NULL || (strcmp(only, "all") == 0 && !sweep) || strcmp(only, tests[i].name) == 0 || (strcmp(only, "everything") == 0)) {
            tests[i].fn();
            ran++;
        }
    }
    printf("%d tests, %d checks, %d failed\n", ran, n_checks, n_failures);
    return n_failures == 0 ? 0 : 1;
}
