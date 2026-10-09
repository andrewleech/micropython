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
#include <time.h>
#include <unistd.h>

#include "mcuboot_config/mcuboot_config.h"
#include "bootutil/bootutil.h"
#include "bootutil/bootutil_public.h"
#include "bootutil/fault_injection_hardening.h"
#include "bootutil/image.h"
#include "bootutil/security_cnt.h"
#include "flash_map_backend/flash_map_backend.h"
#include "sysflash/sysflash.h"
#include "mboot_seccnt.h"
#include "mboot_update.h"
#include "mboot_validate.h"
#include "fake_flash.h"
#include "core.h"

// Scenario and power cut sweep driver for the core glue. Each simulated boot attempt is a forked
// child: the flash (tests/mboot/host/fake_flash.c) lives in shared memory and survives, the
// RAM of the child does not, which is what a reset does. A power cut is the fake flash ending
// the child in the middle of a program or erase, with the partial effect applied.

// ---- images and flash state ----

typedef struct {
    uint8_t *data;
    size_t len;
} blob_t;

static const char *img_dir = "images";
static const char *size_tag = "3s";
static blob_t images[4];    // [0] older image, [1] confirmed initial image v1, [2] update v2, [3] update v3

static bool load_blob(const char *path, blob_t *b) {
    FILE *f = fopen(path, "rb");
    if (f == NULL) {
        fprintf(stderr, "cannot open %s\n", path);
        return false;
    }
    fseek(f, 0, SEEK_END);
    b->len = (size_t)ftell(f);
    fseek(f, 0, SEEK_SET);
    free(b->data);
    b->data = malloc(b->len);
    bool ok = b->data != NULL && fread(b->data, 1, b->len, f) == b->len;
    fclose(f);
    return ok;
}

// Loads the four signed images of a size tag. With swap using offset the update path refuses an
// image of at most one erase unit (mboot_update_min_image()); the images of the sweeps are
// larger than that, as the Makefile makes them.
static bool load_images(const char *tag) {
    size_tag = tag;
    for (int v = 0; v <= 3; v++) {
        char path[512];
        snprintf(path, sizeof(path), "%s/%s_v%d.bin", img_dir, tag, v);
        if (!load_blob(path, &images[v])) {
            return false;
        }
    }
    return true;
}

// Erase unit of the slots.
static uint32_t eu(void) {
    const struct flash_area *pri = NULL;
    flash_area_open(FLASH_AREA_IMAGE_PRIMARY(0), &pri);
    return mboot_area_erase(pri);
}

// Bytes in front of the update image in the update slot: the spare unit of swap using offset.
static uint32_t spare(void) {
    return MBOOT_UPDATE_SPARE;
}

// The slot policies. Swap has a test swap that reverts unless it is confirmed; overwrite-external
// and single install an update for good, and single writes it over the only slot, so an update that
// is cut leaves no image (the device ends in the DFU recovery).
#define HAS_REVERT (MBOOT_POLICY_SWAP)
#define HAS_SECONDARY (!MBOOT_POLICY_SINGLE)

static const struct flash_area *area(uint8_t id) {
    const struct flash_area *fa = NULL;
    flash_area_open(id, &fa);
    return fa;
}

// ---- boot attempt actions (run in the child) ----

static void fail_recover(const char *what, int rc) {
    snprintf(core_shared->out.where, sizeof(core_shared->out.where), "%s: %d", what, rc);
    mboot_fault_recover();
}

static void fill_booted(core_outcome_t *o, const struct boot_rsp *rsp) {
    o->kind = CORE_OUT_BOOTED;
    o->ver_major = rsp->br_hdr->ih_ver.iv_major;
    o->ver_minor = rsp->br_hdr->ih_ver.iv_minor;
    o->ver_rev = rsp->br_hdr->ih_ver.iv_revision;
    o->image_off = rsp->br_image_off;
    #if MBOOT_POLICY_SINGLE
    o->swap_type = BOOT_SWAP_TYPE_NONE;    // the only slot has no swap state
    #else
    o->swap_type = boot_swap_type_multi(0);
    #endif
    #if defined(MBOOT_SECCNT_FLASH)
    mboot_seccnt_flash_read(0, &o->seccnt);
    #endif
}

// What the bootloader does before it hands over: flash checks, scrub, boot_go().
static void c_boot(void) {
    core_outcome_t *o = &core_shared->out;
    int rc = mboot_port_flash_dev_init();
    if (rc == 0) {
        rc = mboot_flash_map_check();
        if (rc != 0) {
            fail_recover("flash map check", rc);
        }
    }
    rc = mboot_flash_scrub();
    if (rc != 0) {
        fail_recover("scrub", rc);
    }
    #if defined(MCUBOOT_HW_ROLLBACK_PROT)
    boot_nv_security_counter_init();
    #endif
    struct boot_rsp rsp;
    FIH_DECLARE(fih_rc, FIH_FAILURE);
    FIH_CALL(boot_go, fih_rc, &rsp);
    if (FIH_NOT_EQ(fih_rc, FIH_SUCCESS)) {
        o->kind = CORE_OUT_NO_IMAGE;
        return;
    }
    #if MBOOT_POLICY_SINGLE && defined(MCUBOOT_HW_ROLLBACK_PROT)
    // What mboot_main() does after boot_go() for the single slot policy.
    if (mboot_validate_raise_counter() != 0) {
        fail_recover("security counter", -1);
    }
    #endif
    fill_booted(o, &rsp);
}

#if !MBOOT_POLICY_SINGLE
static void c_set_pending(void) {
    core_shared->out.rc = boot_set_pending_multi(0, 0);
    core_shared->out.kind = CORE_OUT_DONE;
}
#endif

#if MBOOT_POLICY_SWAP
static void c_confirm(void) {
    core_shared->out.rc = boot_set_confirmed_multi(0);
    core_shared->out.kind = CORE_OUT_DONE;
}
#endif

// What the main flow does before it enters DFU recovery after boot_go() failed or asserted with
// no image that validates: the swap state in the primary slot trailer and the header of the
// primary image are dropped, since nothing there can boot and a header that bootutil takes for
// an image makes the next swap read the damaged image.
static void c_recover(void) {
    const struct flash_area *pri = area(FLASH_AREA_IMAGE_PRIMARY(0));
    int rc = mboot_flash_erase_trailer(FLASH_AREA_IMAGE_PRIMARY(0));
    if (rc == 0) {
        rc = flash_area_erase(pri, 0, eu());
    }
    core_shared->out.rc = rc;
    core_shared->out.kind = CORE_OUT_DONE;
}

#if MBOOT_POLICY_SWAP
static void c_confirm_boot(void) {
    boot_set_confirmed_multi(0);
    c_boot();
}
#endif

static blob_t install_blob;

// The writes of a DFU session on the secondary slot, as the DFU front end does them: the session
// begin of mboot_update_begin() (trailer sectors and spare first sector erased), every sector
// erased before its first block is written, blocks are 2048 bytes, and the manifest step
// marks the image pending.
static void c_dfu_install(void) {
    mboot_update_target_t t;
    uint32_t unit = eu();
    uint32_t wu = mboot_devs[0].write_unit;
    uint32_t fail_off;
    int rc = mboot_update_target(&t);
    if (rc == 0) {
        rc = mboot_update_begin(&t, &fail_off);
    }
    const struct flash_area *sec = t.fap;
    uint32_t pos = 0;
    uint8_t block[2048];
    while (rc == 0 && pos < install_blob.len) {
        uint32_t n = install_blob.len - pos < sizeof(block) ? (uint32_t)(install_blob.len - pos) : (uint32_t)sizeof(block);
        uint32_t addr = t.update_off + pos;
        if (addr % unit == 0) {
            rc = flash_area_erase(sec, addr, unit);
            if (rc != 0) {
                break;
            }
        }
        memset(block, 0xFF, sizeof(block));
        memcpy(block, install_blob.data + pos, n);
        uint32_t padded = (n + wu - 1) / wu * wu;
        rc = mboot_flash_dev_write(sec->fa_device_id, sec->fa_off + addr, block, padded);
        pos += n;
    }
    if (rc == 0) {
        rc = mboot_update_mark_pending(false);
    }
    core_shared->out.rc = rc;
    core_shared->out.kind = CORE_OUT_DONE;
}

// ---- running a child ----

typedef struct {
    bool cut;           // ended by an injected power cut
    bool reset;         // mboot_port_reset()
    bool recovery;      // mboot_fault_recover()
    bool hang;          // killed by the alarm
    bool crash;         // any other signal
    int signal;
    uint32_t ops;       // flash operations of the child
    core_outcome_t out;
} run_t;

static run_t run_child(void (*fn)(void)) {
    memset(&core_shared->out, 0, sizeof(core_shared->out));
    fflush(stdout);
    uint32_t ops0 = fake_flash_op_count();
    pid_t pid = fork();
    if (pid == 0) {
        alarm(60);
        fn();
        _exit(0);
    }
    int status = 0;
    waitpid(pid, &status, 0);
    run_t r;
    memset(&r, 0, sizeof(r));
    r.out = core_shared->out;
    r.ops = fake_flash_op_count() - ops0;
    if (WIFEXITED(status)) {
        r.cut = WEXITSTATUS(status) == FAKE_FLASH_CUT_EXIT;
        r.reset = WEXITSTATUS(status) == CORE_EXIT_RESET;
        r.recovery = WEXITSTATUS(status) == CORE_EXIT_RECOVERY;
        if (!r.cut && !r.reset && !r.recovery && WEXITSTATUS(status) != 0) {
            r.crash = true;
            r.signal = -WEXITSTATUS(status);
        }
    } else if (WIFSIGNALED(status)) {
        r.signal = WTERMSIG(status);
        r.hang = r.signal == SIGALRM;
        r.crash = !r.hang;
    }
    return r;
}

static const char *describe(const run_t *r) {
    static char buf[160];
    if (r->cut) {
        return "cut";
    }
    if (r->hang) {
        return "hang";
    }
    if (r->crash) {
        snprintf(buf, sizeof(buf), "crash signal %d", r->signal);
        return buf;
    }
    if (r->recovery) {
        snprintf(buf, sizeof(buf), "recovery (%s)", r->out.where);
        return buf;
    }
    switch (r->out.kind) {
        case CORE_OUT_BOOTED:
            snprintf(buf, sizeof(buf), "booted v%u swap %d", r->out.ver_major, r->out.swap_type);
            return buf;
        case CORE_OUT_NO_IMAGE:
            return "no image";
        case CORE_OUT_DONE:
            snprintf(buf, sizeof(buf), "done rc=%d", r->out.rc);
            return buf;
        default:
            return "no outcome";
    }
}

static bool booted(const run_t *r) {
    return !r->cut && !r->recovery && !r->hang && !r->crash && r->out.kind == CORE_OUT_BOOTED;
}

static bool dfu_install_works(void);

// ---- states ----

static void poke_v1(void) {
    const struct flash_area *pri = area(FLASH_AREA_IMAGE_PRIMARY(0));
    fake_flash_poke(0, pri->fa_off, images[1].data, images[1].len);
}

#if !MBOOT_POLICY_SINGLE
static void poke_update(int v) {
    const struct flash_area *sec = area(FLASH_AREA_IMAGE_SECONDARY(0));
    fake_flash_poke(0, sec->fa_off + spare(), images[v].data, images[v].len);
}
#endif

typedef struct {
    fake_flash_snapshot_t *factory;     // v1 programmed from outside, never booted
    fake_flash_snapshot_t *v1;          // v1 booted once (scrub done), nothing pending
    fake_flash_snapshot_t *written;     // v2 written to the secondary slot, not pending
    fake_flash_snapshot_t *pending;     // v2 pending (test swap requested)
    fake_flash_snapshot_t *swapped;     // test swap done, v2 booted and unconfirmed
} states_t;

static states_t st;

static void free_states(void) {
    fake_flash_snapshot_t **all[] = {&st.factory, &st.v1, &st.written, &st.pending, &st.swapped};
    for (size_t i = 0; i < sizeof(all) / sizeof(all[0]); i++) {
        if (*all[i] != NULL) {
            fake_flash_snapshot_free(*all[i]);
            *all[i] = NULL;
        }
    }
}

static bool build_states(void) {
    free_states();
    core_flash_fresh();
    poke_v1();
    st.factory = fake_flash_snapshot();

    run_t r = run_child(c_boot);
    if (!CORE_CHECK(booted(&r) && r.out.ver_major == 1)) {
        printf("  setup: first boot: %s\n", describe(&r));
        return false;
    }
    st.v1 = fake_flash_snapshot();

    #if MBOOT_POLICY_SINGLE
    // The update is written over the only slot, there is nothing pending: the states of an
    // update that was written, is pending and is done are the state of the installed image.
    install_blob = images[2];
    r = run_child(c_dfu_install);
    if (!CORE_CHECK(r.out.kind == CORE_OUT_DONE && r.out.rc == 0)) {
        return false;
    }
    st.written = fake_flash_snapshot();
    st.pending = fake_flash_snapshot();
    #else
    poke_update(2);
    st.written = fake_flash_snapshot();
    r = run_child(c_set_pending);
    if (!CORE_CHECK(r.out.kind == CORE_OUT_DONE && r.out.rc == 0)) {
        return false;
    }
    st.pending = fake_flash_snapshot();
    #endif

    r = run_child(c_boot);
    if (!CORE_CHECK(booted(&r) && r.out.ver_major == 2)) {
        printf("  setup: swap boot: %s\n", describe(&r));
        return false;
    }
    st.swapped = fake_flash_snapshot();
    return true;
}

// ---- functional scenarios ----

static uint32_t header_version_at(uint32_t dev_off) {
    struct image_header h;
    fake_flash_peek(0, dev_off, &h, sizeof(h));
    return h.ih_magic == IMAGE_MAGIC ? h.ih_ver.iv_major : 0;
}

#if MBOOT_POLICY_SWAP

static void test_functional(void) {
    printf("functional scenarios (size %s)\n", size_tag);
    if (!build_states()) {
        return;
    }
    const struct flash_area *pri = area(FLASH_AREA_IMAGE_PRIMARY(0));
    const struct flash_area *sec = area(FLASH_AREA_IMAGE_SECONDARY(0));
    run_t r;

    // Good swap. v2 runs in test mode; the old image is in the secondary slot, at its start.
    fake_flash_restore(st.pending);
    r = run_child(c_boot);
    CORE_CHECK(booted(&r) && r.out.ver_major == 2 && r.out.swap_type == BOOT_SWAP_TYPE_REVERT);
    CORE_CHECK(header_version_at(pri->fa_off) == 2);
    CORE_CHECK(header_version_at(sec->fa_off) == 1);
    #if defined(MBOOT_SECCNT_FLASH)
    CORE_CHECK(r.out.seccnt == 0);    // a test image does not raise the counter
    #endif
    printf("  good swap: %s\n", describe(&r));

    // Confirm. v2 stays, no swap, the counter follows the image.
    r = run_child(c_confirm);
    CORE_CHECK(r.out.kind == CORE_OUT_DONE && r.out.rc == 0);
    r = run_child(c_boot);
    CORE_CHECK(booted(&r) && r.out.ver_major == 2 && r.out.swap_type == BOOT_SWAP_TYPE_NONE);
    #if defined(MBOOT_SECCNT_FLASH)
    CORE_CHECK(r.out.seccnt == 1);
    #endif
    printf("  confirm: %s\n", describe(&r));
    r = run_child(c_boot);
    CORE_CHECK(booted(&r) && r.out.ver_major == 2 && r.out.swap_type == BOOT_SWAP_TYPE_NONE);

    // Revert. Reset without confirming: v1 runs again and v2 is back in the secondary slot.
    fake_flash_restore(st.swapped);
    r = run_child(c_boot);
    CORE_CHECK(booted(&r) && r.out.ver_major == 1 && r.out.swap_type == BOOT_SWAP_TYPE_NONE);
    CORE_CHECK(header_version_at(pri->fa_off) == 1);
    CORE_CHECK(header_version_at(sec->fa_off + spare()) == 2);
    #if defined(MBOOT_SECCNT_FLASH)
    CORE_CHECK(r.out.seccnt == 0);
    #endif
    printf("  revert: %s\n", describe(&r));
    r = run_child(c_boot);
    CORE_CHECK(booted(&r) && r.out.ver_major == 1 && r.out.swap_type == BOOT_SWAP_TYPE_NONE);

    // A pending image whose body was changed after signing is ignored; v1 keeps running.
    fake_flash_restore(st.written);
    uint8_t flip = 0;
    fake_flash_peek(0, sec->fa_off + spare() + 0x500, &flip, 1);
    flip ^= 0x20;
    fake_flash_poke(0, sec->fa_off + spare() + 0x500, &flip, 1);
    r = run_child(c_set_pending);
    CORE_CHECK(r.out.rc == 0);
    r = run_child(c_boot);
    CORE_CHECK(booted(&r) && r.out.ver_major == 1 && r.out.swap_type == BOOT_SWAP_TYPE_NONE);
    r = run_child(c_boot);
    CORE_CHECK(booted(&r) && r.out.ver_major == 1);
    printf("  tampered update ignored: %s\n", describe(&r));

    // A downgrade is refused: after v2 is confirmed, an older update (lower version and lower
    // security counter) is not swapped in, whichever of the two checks the board uses.
    fake_flash_restore(st.swapped);
    run_child(c_confirm);
    r = run_child(c_boot);
    CORE_CHECK(booted(&r) && r.out.ver_major == 2 && r.out.swap_type == BOOT_SWAP_TYPE_NONE);
    fake_flash_erase_raw(0, sec->fa_off, sec->fa_size);
    fake_flash_poke(0, sec->fa_off + spare(), images[0].data, images[0].len);
    r = run_child(c_set_pending);
    CORE_CHECK(r.out.rc == 0);
    r = run_child(c_boot);
    CORE_CHECK(booted(&r) && r.out.ver_major == 2 && r.out.swap_type == BOOT_SWAP_TYPE_NONE);
    r = run_child(c_boot);
    CORE_CHECK(booted(&r) && r.out.ver_major == 2 && r.out.swap_type == BOOT_SWAP_TYPE_NONE);
    printf("  downgrade refused: %s\n", describe(&r));
}

#elif MBOOT_POLICY_OVERWRITE_EXTERNAL

// Overwrite-external: the update is copied over the primary slot and is final. There is no test
// swap, no revert and nothing to confirm.
static void test_functional(void) {
    printf("functional scenarios (size %s)\n", size_tag);
    if (!build_states()) {
        return;
    }
    const struct flash_area *pri = area(FLASH_AREA_IMAGE_PRIMARY(0));
    const struct flash_area *sec = area(FLASH_AREA_IMAGE_SECONDARY(0));
    run_t r;

    fake_flash_restore(st.pending);
    r = run_child(c_boot);
    CORE_CHECK(booted(&r) && r.out.ver_major == 2 && r.out.swap_type == BOOT_SWAP_TYPE_NONE);
    CORE_CHECK(header_version_at(pri->fa_off) == 2);
    #if defined(MBOOT_SECCNT_FLASH)
    CORE_CHECK(r.out.seccnt == 1);    // the update is permanent: the counter follows the image
    #endif
    printf("  copy: %s\n", describe(&r));
    r = run_child(c_boot);
    CORE_CHECK(booted(&r) && r.out.ver_major == 2 && r.out.swap_type == BOOT_SWAP_TYPE_NONE);

    // A pending image whose body was changed after signing is ignored; v1 keeps running.
    fake_flash_restore(st.written);
    uint8_t flip = 0;
    fake_flash_peek(0, sec->fa_off + 0x500, &flip, 1);
    flip ^= 0x20;
    fake_flash_poke(0, sec->fa_off + 0x500, &flip, 1);
    r = run_child(c_set_pending);
    CORE_CHECK(r.out.rc == 0);
    r = run_child(c_boot);
    CORE_CHECK(booted(&r) && r.out.ver_major == 1);
    r = run_child(c_boot);
    CORE_CHECK(booted(&r) && r.out.ver_major == 1);
    printf("  tampered update ignored: %s\n", describe(&r));

    // A downgrade is refused after v2 has been installed.
    fake_flash_restore(st.swapped);
    fake_flash_erase_raw(0, sec->fa_off, sec->fa_size);
    fake_flash_poke(0, sec->fa_off, images[0].data, images[0].len);
    r = run_child(c_set_pending);
    CORE_CHECK(r.out.rc == 0);
    r = run_child(c_boot);
    CORE_CHECK(booted(&r) && r.out.ver_major == 2);
    r = run_child(c_boot);
    CORE_CHECK(booted(&r) && r.out.ver_major == 2);
    printf("  downgrade refused: %s\n", describe(&r));
}

#else

// Single slot: an update is written over the only slot, as the DFU session and fsload do. A
// finished install boots; an image that fails validation is never started, the device is left
// without an image and the DFU front end installs a new one.
static void test_functional(void) {
    printf("functional scenarios (size %s)\n", size_tag);
    if (!build_states()) {
        return;
    }
    const struct flash_area *pri = area(FLASH_AREA_IMAGE_PRIMARY(0));
    run_t r;

    fake_flash_restore(st.v1);
    install_blob = images[2];
    r = run_child(c_dfu_install);
    CORE_CHECK(r.out.kind == CORE_OUT_DONE && r.out.rc == 0);
    r = run_child(c_boot);
    CORE_CHECK(booted(&r) && r.out.ver_major == 2 && r.out.swap_type == BOOT_SWAP_TYPE_NONE);
    CORE_CHECK(header_version_at(pri->fa_off) == 2);
    #if defined(MBOOT_SECCNT_FLASH)
    CORE_CHECK(r.out.seccnt == 1);    // there is no test image: the counter follows the image
    #endif
    printf("  install: %s\n", describe(&r));
    r = run_child(c_boot);
    CORE_CHECK(booted(&r) && r.out.ver_major == 2);

    // An image whose body was changed after signing is not started.
    fake_flash_restore(st.swapped);
    uint8_t flip = 0;
    fake_flash_peek(0, pri->fa_off + 0x500, &flip, 1);
    flip ^= 0x20;
    fake_flash_poke(0, pri->fa_off + 0x500, &flip, 1);
    r = run_child(c_boot);
    CORE_CHECK(!booted(&r));
    printf("  tampered image not started: %s\n", describe(&r));
    run_child(c_recover);
    CORE_CHECK(dfu_install_works());

    // A downgrade is refused. The only slot cannot fall back to the old image, so the device is
    // left without an image and a new install recovers it.
    fake_flash_restore(st.swapped);
    r = run_child(c_boot);
    CORE_CHECK(booted(&r) && r.out.ver_major == 2);
    install_blob = images[0];
    r = run_child(c_dfu_install);
    CORE_CHECK(r.out.kind == CORE_OUT_DONE && r.out.rc == 0);
    r = run_child(c_boot);
    CORE_CHECK(!booted(&r));
    printf("  downgrade refused: %s\n", describe(&r));
    run_child(c_recover);
    CORE_CHECK(dfu_install_works());
}

#endif

// The fault injection state in the request RAM (MBOOT_TEST_FI) must cut at the same place as
// the fake flash does: the same operations are counted, and a reset before or after operation k
// leaves the same flash contents as a power cut before or after it.
static void test_fi_equivalence(void) {
    printf("fault injection hook against the fake flash cuts (size %s)\n", size_tag);
    uint32_t *fi = (uint32_t *)(core_shared->req_ram + 0x3F0);
    uint32_t dev_size = mboot_devs[0].size;
    uint8_t *ref = malloc(dev_size);

    // The boot of a pending update; for the single slot policy the install of an update, since
    // a boot does not write anything there.
    fake_flash_snapshot_t *start = MBOOT_POLICY_SINGLE ? st.v1 : st.pending;
    void (*action)(void) = MBOOT_POLICY_SINGLE ? c_dfu_install : c_boot;
    install_blob = images[2];
    fake_flash_restore(start);
    fi[0] = 0x4A4E4946u;
    fi[1] = 0;
    fi[2] = 0;
    fi[3] = 0;
    run_child(action);
    uint32_t m = fake_flash_op_count();
    CORE_CHECK(fi[2] == m && m > 10);

    unsigned mismatches = 0;
    for (uint32_t k = 1; k <= m; k++) {
        for (uint32_t mode = 0; mode < 2; mode++) {
            memset(fi, 0, 16);
            fake_flash_restore(start);
            fake_flash_arm(k, mode == 0 ? FAKE_FLASH_CUT_BEFORE : FAKE_FLASH_CUT_AFTER, 0, 0);
            run_t a = run_child(action);
            fake_flash_disarm();
            memcpy(ref, fake_flash_data(0), dev_size);

            fake_flash_restore(start);
            fi[0] = 0x4A4E4946u;
            fi[1] = k;
            fi[2] = 0;
            fi[3] = mode;
            run_t b = run_child(action);
            if (!a.cut || !b.reset || memcmp(ref, fake_flash_data(0), dev_size) != 0) {
                mismatches++;
            }
        }
    }
    memset(fi, 0, 16);
    CORE_CHECK(mismatches == 0);
    printf("  %u operations, %u cut points compared, %u mismatches\n", m, 2 * m, mismatches);
    free(ref);
}

static run_t boot_until_image(unsigned *asserts, unsigned *no_images, unsigned *halts);

// No bootable image: the primary image header is gone. A DFU install into the secondary slot has
// to bring the device back. bootutil only accepts the install when it is built with
// MCUBOOT_BOOTSTRAP; the single slot policy installs over the primary slot itself.
static void test_no_image_recovery(void) {
    #if defined(MCUBOOT_BOOTSTRAP) || MBOOT_POLICY_SINGLE
    printf("no bootable image, DFU install (size %s)\n", size_tag);
    const struct flash_area *pri = area(FLASH_AREA_IMAGE_PRIMARY(0));
    fake_flash_restore(st.v1);
    fake_flash_erase_raw(0, pri->fa_off, eu());
    run_t r = run_child(c_boot);
    CORE_CHECK(!booted(&r));
    printf("  header erased: %s\n", describe(&r));
    install_blob = images[2];
    r = run_child(c_dfu_install);
    CORE_CHECK(r.out.kind == CORE_OUT_DONE && r.out.rc == 0);
    unsigned a = 0, n = 0, h = 0;
    r = boot_until_image(&a, &n, &h);
    printf("  after the install: %s\n", describe(&r));
    CORE_CHECK(booted(&r) && r.out.ver_major == 2);
    #else
    printf("no bootable image, DFU install: skipped, needs MCUBOOT_BOOTSTRAP or the single slot policy\n");
    #endif
}

// ---- sweeps ----

typedef enum {
    SK_SWAP,        // test swap of a pending image
    SK_REVERT,      // revert of an unconfirmed image
    SK_CONFIRM,     // confirm and the boot that follows
    SK_PENDING,     // setting an image pending
    SK_DFU,         // a DFU session on the secondary slot
    SK_INIT,        // first boot of a device programmed from outside
} scn_kind_t;

typedef struct {
    const char *name;
    scn_kind_t kind;
    fake_flash_snapshot_t **start;
    void (*action)(void);
    int install_version;    // image the DFU action installs
} scenario_t;

typedef struct {
    unsigned runs;
    unsigned swapped;       // first boot after the cut shows the new image
    unsigned abandoned;     // the update was dropped or deferred, old image intact
    unsigned lost_revert;
    unsigned lost_image;
    unsigned asserts;       // assert or flash check failure that led to recovery
    unsigned halts;         // hang or crash
    unsigned no_dfu;
    unsigned not_converged;
    unsigned inconsistent;
} tally_t;

typedef struct {
    char text[400];
} failure_t;

#define MAX_FAILURES 14
static failure_t failures[MAX_FAILURES];
static unsigned n_failures;

static void note_failure(const char *scn, const char *group, uint32_t k, const char *mode, const char *what) {
    if (n_failures < MAX_FAILURES) {
        snprintf(failures[n_failures].text, sizeof(failures[n_failures].text), "%s %s k=%u %s: %s", scn, group, k, mode, what);
    }
    n_failures++;
}

typedef struct {
    fake_flash_cut_mode_t mode;
    uint32_t q8;
    uint32_t seed;
} variant_t;

static const char *mode_name(const variant_t *v) {
    static char buf[48];
    static const char *names[] = {"before", "after", "during", "between"};
    if (v->mode == FAKE_FLASH_CUT_DURING) {
        snprintf(buf, sizeof(buf), "during q=%u seed=%u", v->q8, v->seed);
    } else if (v->mode == FAKE_FLASH_CUT_WEAK) {
        snprintf(buf, sizeof(buf), "corrected q=%u seed=%u", v->q8, v->seed);
    } else if (v->mode == FAKE_FLASH_CUT_BETWEEN) {
        snprintf(buf, sizeof(buf), "between q=%u", v->q8);
    } else {
        snprintf(buf, sizeof(buf), "%s", names[v->mode]);
    }
    return buf;
}

typedef struct {
    int stride_k;           // every n-th operation (1 = all)
    int stride_dfu;         // DFU reachability check on every n-th case (0 = never)
    int torn_variants;      // 1..5
    bool verbose;
    bool show_trace;
    bool list_cases;        // print every case that is not an ordinary success
    int stride_k1;          // double cut sweep: every n-th first cut
    int stride_k2;          // double cut sweep: every n-th second cut
    bool double_clean;      // double cut sweep with clean cuts (before the operation) instead of torn words
} sweep_opts_t;

static sweep_opts_t opts = {1, 4, 3, false, false, false, 3, 1, false};

// Boot until an image runs, with at most six attempts. Returns the last run.
static run_t boot_until_image(unsigned *asserts, unsigned *no_images, unsigned *halts) {
    run_t r;
    memset(&r, 0, sizeof(r));
    for (int i = 0; i < 6; i++) {
        r = run_child(c_boot);
        if (booted(&r)) {
            return r;
        }
        if (r.hang || r.crash) {
            (*halts)++;
            return r;
        }
        if (r.recovery) {
            (*asserts)++;
            return r;
        }
        if (r.out.kind == CORE_OUT_NO_IMAGE) {
            (*no_images)++;
            return r;
        }
    }
    return r;
}

// DFU reachability: install a new image the way the DFU front end does and boot it.
static bool dfu_install_works(void) {
    install_blob = images[3];
    run_t r = run_child(c_dfu_install);
    if (r.cut || r.hang || r.crash || r.recovery || r.out.kind != CORE_OUT_DONE || r.out.rc != 0) {
        return false;
    }
    unsigned a = 0, n = 0, h = 0;
    r = boot_until_image(&a, &n, &h);
    return booted(&r) && r.out.ver_major == 3;
}

typedef struct {
    bool lost_image, assert_rec, halt, no_dfu, lost_revert, abandoned, swapped, not_conv, inconsistent;
    char what[160];
} verdict_t;

// Settles the device after a cut (or a cut free run) and applies the predicates of the scenario.
static verdict_t settle_and_judge(const scenario_t *s, bool dfu_check) {
    verdict_t v;
    memset(&v, 0, sizeof(v));
    unsigned asserts = 0, no_images = 0, halts = 0;
    run_t first = boot_until_image(&asserts, &no_images, &halts);
    v.assert_rec = asserts != 0;
    v.lost_image = no_images != 0;
    v.halt = halts != 0;
    bool recovery_state = !booted(&first);
    if (recovery_state) {
        snprintf(v.what, sizeof(v.what), "%s", describe(&first));
        v.not_conv = !(v.assert_rec || v.lost_image || v.halt);
        run_child(c_recover);
    } else {
        unsigned fv = first.out.ver_major;
        run_t second = run_child(c_boot);
        run_t third = run_child(c_boot);
        unsigned sv = booted(&second) ? second.out.ver_major : 0;
        unsigned tv = booted(&third) ? third.out.ver_major : 0;
        snprintf(v.what, sizeof(v.what), "first v%u, then v%u, v%u", fv, sv, tv);
        switch (s->kind) {
            case SK_SWAP:
            case SK_PENDING:
            case SK_DFU:
                if (fv == 2) {
                    v.swapped = true;
                    if (HAS_REVERT) {
                        if (sv != 1 || tv != 1) {
                            v.lost_revert = true;
                        }
                    } else if (sv != 2 || tv != 2) {
                        // The update is final: the image that runs stays.
                        v.inconsistent = true;
                    }
                } else if (fv == 1) {
                    v.abandoned = true;
                    if (sv != 1 || tv != 1) {
                        v.inconsistent = true;
                    }
                } else {
                    v.inconsistent = true;
                }
                break;
            case SK_REVERT:
                if (fv == 1) {
                    v.swapped = true;
                    if (sv != 1 || tv != 1) {
                        v.inconsistent = true;
                    }
                } else if (fv == 2 && sv == 1 && tv == 1) {
                    v.abandoned = true;     // the revert needed one more boot
                } else {
                    v.lost_revert = true;
                }
                break;
            case SK_CONFIRM:
                if (fv == 2) {
                    v.swapped = true;
                    if (!((sv == 2 && tv == 2) || (sv == 1 && tv == 1))) {
                        v.inconsistent = true;
                    }
                } else if (fv == 1) {
                    v.abandoned = true;
                    if (sv != 1 || tv != 1) {
                        v.inconsistent = true;
                    }
                } else {
                    v.inconsistent = true;
                }
                break;
            case SK_INIT:
                if (fv == 1) {
                    v.swapped = true;
                    if (sv != 1 || tv != 1) {
                        v.inconsistent = true;
                    }
                } else {
                    v.inconsistent = true;
                }
                break;
        }
    }
    // The DFU session must work from every state, in particular from the ones that lost the image.
    if (dfu_check || recovery_state) {
        if (!dfu_install_works()) {
            v.no_dfu = true;
        }
    }
    return v;
}

static void tally_add(tally_t *t, const verdict_t *v) {
    t->runs++;
    t->swapped += v->swapped;
    t->abandoned += v->abandoned;
    t->lost_revert += v->lost_revert;
    t->lost_image += v->lost_image;
    t->asserts += v->assert_rec;
    t->halts += v->halt;
    t->no_dfu += v->no_dfu;
    t->not_converged += v->not_conv;
    t->inconsistent += v->inconsistent;
}

// With the single slot policy an update that is cut leaves no image: there is nothing to fall back
// to. That is the expected outcome: boot_go() refuses to start an image that does not validate,
// the device is in the recovery front end, and no_dfu checks that a DFU install brings it back. A
// lost image is not a failure there.
static bool verdict_failed(const verdict_t *v) {
    return (v->lost_image && !MBOOT_POLICY_SINGLE) || v->assert_rec || v->halt || v->no_dfu || v->lost_revert || v->not_conv || v->inconsistent;
}

// Operation classes of the trace, by area.
typedef enum {
    CL_PRI_BODY, CL_PRI_TRAILER, CL_SEC_SPARE, CL_SEC_BODY, CL_SEC_TRAILER, CL_SHADOW, CL_SECCNT, CL_SCRATCH, CL_INTENT, CL_OTHER, CL_COUNT
} op_class_t;

static const char *const class_name[CL_COUNT] = {
    "primary body", "primary trailer", "secondary spare", "secondary body", "secondary trailer", "shadow", "seccnt", "scratch", "intent", "other",
};

static op_class_t classify(const fake_flash_op_t *op) {
    const struct flash_area *pri = area(FLASH_AREA_IMAGE_PRIMARY(0));
    const struct flash_area *sec = area(FLASH_AREA_IMAGE_SECONDARY(0));
    uint32_t unit = eu();
    // The only slot of the single policy has no trailer sectors.
    uint32_t tsec = MBOOT_POLICY_SINGLE ? 0 : MBOOT_TRAILER_SECTORS;
    uint32_t o = op->off;
    if (o >= pri->fa_off && o < pri->fa_off + pri->fa_size) {
        return o >= pri->fa_off + pri->fa_size - tsec * unit ? CL_PRI_TRAILER : CL_PRI_BODY;
    }
    if (o >= sec->fa_off && o < sec->fa_off + sec->fa_size) {
        if (o < sec->fa_off + spare()) {
            return CL_SEC_SPARE;
        }
        return o >= sec->fa_off + sec->fa_size - tsec * unit ? CL_SEC_TRAILER : CL_SEC_BODY;
    }
    const struct flash_area *a;
    #if defined(MBOOT_ECC_SHADOW)
    a = area(MBOOT_AREA_SHADOW);
    if (a != NULL && o >= a->fa_off && o < a->fa_off + a->fa_size) {
        return CL_SHADOW;
    }
    #endif
    a = area(MBOOT_AREA_SECCNT);
    if (a != NULL && o >= a->fa_off && o < a->fa_off + a->fa_size) {
        return CL_SECCNT;
    }
    a = area(FLASH_AREA_IMAGE_SCRATCH);
    if (a != NULL && o >= a->fa_off && o < a->fa_off + a->fa_size) {
        return CL_SCRATCH;
    }
    a = area(MBOOT_AREA_INTENT);
    if (a != NULL && o >= a->fa_off && o < a->fa_off + a->fa_size) {
        return CL_INTENT;
    }
    return CL_OTHER;
}

typedef struct {
    unsigned ops[CL_COUNT];     // operations of the baseline run in the class
    unsigned runs[CL_COUNT];
    unsigned failed[CL_COUNT];
} class_tally_t;

typedef struct {
    char label[64];
    unsigned ops;
    tally_t clean;
    tally_t torn;
    class_tally_t cls;
} scn_report_t;

#define MAX_REPORTS 32
static scn_report_t reports[MAX_REPORTS];
static unsigned n_reports;

static void run_case(const scenario_t *s, scn_report_t *rep, bool torn, uint32_t k, const fake_flash_op_t *op,
    const variant_t *var, bool dfu_check) {
    fake_flash_restore(*s->start);
    fake_flash_arm(k, var->mode, var->q8, var->seed);
    install_blob = images[s->install_version];
    run_t r = run_child(s->action);
    fake_flash_disarm();
    verdict_t v;
    if (!r.cut && !r.hang && !r.crash && !r.reset && !r.recovery) {
        // The cut point lies beyond the run (after the last operation): nothing was cut.
        v = settle_and_judge(s, dfu_check);
    } else if (r.hang || r.crash) {
        memset(&v, 0, sizeof(v));
        v.halt = true;
        snprintf(v.what, sizeof(v.what), "%s", describe(&r));
    } else if (r.recovery) {
        v = settle_and_judge(s, true);
        v.assert_rec = true;
        snprintf(v.what, sizeof(v.what), "recovery before the cut (%s)", r.out.where);
    } else {
        v = settle_and_judge(s, dfu_check);
    }
    tally_t *t = torn ? &rep->torn : &rep->clean;
    tally_add(t, &v);
    if (opts.list_cases && v.abandoned) {
        printf("    %s %s k=%u (%s) %s: %s\n", s->name, torn ? "torn" : "clean", k, op->kind == FAKE_FLASH_OP_ERASE ? "erase" : "write",
            mode_name(var), v.what);
    }
    op_class_t c = classify(op);
    rep->cls.runs[c]++;
    if (verdict_failed(&v)) {
        rep->cls.failed[c]++;
        char what[300];
        snprintf(what, sizeof(what), "on %s %s: %s", class_name[c], op->kind == FAKE_FLASH_OP_ERASE ? "erase" : "write", v.what);
        note_failure(s->name, torn ? "torn" : "clean", k, mode_name(var), what);
    }
}

// Runs the action once without a cut to get the operation count and trace, then sweeps.
static void sweep(const scenario_t *s, bool clean, bool torn_group) {
    if (n_reports >= MAX_REPORTS) {
        return;
    }
    scn_report_t *rep = &reports[n_reports++];
    memset(rep, 0, sizeof(*rep));
    snprintf(rep->label, sizeof(rep->label), "%s %s", s->name, size_tag);

    fake_flash_restore(*s->start);
    install_blob = images[s->install_version];
    run_t base = run_child(s->action);
    uint32_t m = fake_flash_op_count();
    if (base.cut || base.hang || base.crash) {
        printf("  %s: baseline run failed: %s\n", rep->label, describe(&base));
        core_failures++;
        return;
    }
    fake_flash_op_t *trace = malloc(m * sizeof(*trace));
    size_t have = fake_flash_trace_count();
    if (have < m) {
        printf("  %s: trace has %zu of %u operations\n", rep->label, have, m);
        core_failures++;
        free(trace);
        return;
    }
    memcpy(trace, fake_flash_trace(), m * sizeof(*trace));
    rep->ops = m;
    for (uint32_t i = 0; i < m; i++) {
        rep->cls.ops[classify(&trace[i])]++;
        if (opts.show_trace) {
            printf("    %s op %3u %s %-18s dev off 0x%06x len 0x%x\n", rep->label, i + 1, trace[i].kind == FAKE_FLASH_OP_ERASE ? "erase" : "write",
                class_name[classify(&trace[i])], trace[i].off, trace[i].len);
        }
    }
    uint32_t wu = mboot_devs[0].write_unit;

    unsigned idx = 0;
    clock_t t0 = clock();
    for (uint32_t k = 1; k <= m; k += (uint32_t)opts.stride_k) {
        const fake_flash_op_t *op = &trace[k - 1];
        if (clean) {
            variant_t vs[3] = {{FAKE_FLASH_CUT_BEFORE, 0, 0}, {FAKE_FLASH_CUT_BETWEEN, 128, 0}, {FAKE_FLASH_CUT_AFTER, 0, 0}};
            int n = k == m ? 3 : 2;
            for (int i = 0; i < n; i++) {
                bool dfu = opts.stride_dfu != 0 && (idx % (unsigned)opts.stride_dfu) == 0;
                run_case(s, rep, false, k, op, &vs[i], dfu);
                idx++;
            }
        }
        if (torn_group) {
            variant_t vs[7];
            int n = opts.torn_variants;
            if (op->kind == FAKE_FLASH_OP_WRITE && op->len == wu) {
                for (int i = 0; i < 5; i++) {
                    vs[i] = (variant_t) {FAKE_FLASH_CUT_DURING, 128, (uint32_t)i + 1};
                }
            } else if (op->kind == FAKE_FLASH_OP_WRITE) {
                static const uint32_t q[5] = {32, 128, 224, 1, 255};
                for (int i = 0; i < 5; i++) {
                    vs[i] = (variant_t) {FAKE_FLASH_CUT_DURING, q[i], (uint32_t)i + 1};
                }
            } else {
                static const uint32_t q[5] = {1, 128, 255, 64, 192};
                for (int i = 0; i < 5; i++) {
                    vs[i] = (variant_t) {FAKE_FLASH_CUT_DURING, q[i], (uint32_t)i + 1};
                }
            }
            // A cut at the start of a program pulse leaves a unit that reads corrected, with the
            // erased value (odd seed) or a partial program (even seed): always run both.
            vs[5] = (variant_t) {FAKE_FLASH_CUT_WEAK, 0, 2};
            vs[6] = (variant_t) {FAKE_FLASH_CUT_WEAK, 0, 3};
            for (int j = 0; j < n + 2; j++) {
                int i = j < n ? j : 5 + (j - n);
                bool dfu = opts.stride_dfu != 0 && (idx % (unsigned)opts.stride_dfu) == 0;
                run_case(s, rep, true, k, op, &vs[i], dfu);
                idx++;
            }
        }
    }
    free(trace);
    printf("  %-28s %4u operations, %5u runs, %.1f s\n", rep->label, m, rep->clean.runs + rep->torn.runs,
        (double)(clock() - t0) / CLOCKS_PER_SEC);
    fflush(stdout);
}

static void print_tally_header(void) {
    printf("%-22s %-6s %6s %7s %9s %11s %10s %7s %6s %7s %8s %7s\n", "scenario", "cuts", "runs", "new/ok", "abandoned", "lost revert",
        "lost image", "assert", "halts", "no DFU", "not conv", "incons");
}

static void print_tally(const char *label, const char *group, const tally_t *t) {
    if (t->runs == 0) {
        return;
    }
    printf("%-22s %-6s %6u %7u %9u %11u %10u %7u %6u %7u %8u %7u\n", label, group, t->runs, t->swapped, t->abandoned, t->lost_revert,
        t->lost_image, t->asserts, t->halts, t->no_dfu, t->not_converged, t->inconsistent);
}

static unsigned report_totals(tally_t *clean, tally_t *torn) {
    memset(clean, 0, sizeof(*clean));
    memset(torn, 0, sizeof(*torn));
    unsigned *cp = (unsigned *)clean;
    unsigned *tp = (unsigned *)torn;
    for (unsigned i = 0; i < n_reports; i++) {
        const unsigned *a = (const unsigned *)&reports[i].clean;
        const unsigned *b = (const unsigned *)&reports[i].torn;
        for (size_t j = 0; j < sizeof(tally_t) / sizeof(unsigned); j++) {
            cp[j] += a[j];
            tp[j] += b[j];
        }
    }
    return n_reports;
}

// Prints the table, the class coverage and the first failures; returns the number of
// halts, lost reverts, no-DFU states and asserts (the gating counts).
static unsigned print_report(const char *title) {
    printf("\n== %s ==\n", title);
    #if MBOOT_POLICY_SINGLE
    printf("single slot policy: no revert; a cut update leaves no image (\"lost image\" is the specified fail closed\n"
        "outcome: nothing is started, the device is in the recovery front end, \"no DFU\" counts the cases where\n"
        "a DFU install does not bring it back)\n");
    #endif
    print_tally_header();
    for (unsigned i = 0; i < n_reports; i++) {
        print_tally(reports[i].label, "clean", &reports[i].clean);
        print_tally(reports[i].label, "torn", &reports[i].torn);
    }
    tally_t c, t;
    report_totals(&c, &t);
    print_tally("TOTAL", "clean", &c);
    print_tally("TOTAL", "torn", &t);

    // Operations by class over all scenarios and the cuts that landed on them.
    unsigned ops[CL_COUNT] = {0}, runs[CL_COUNT] = {0}, failed[CL_COUNT] = {0};
    for (unsigned i = 0; i < n_reports; i++) {
        for (int ci = 0; ci < CL_COUNT; ci++) {
            ops[ci] += reports[i].cls.ops[ci];
            runs[ci] += reports[i].cls.runs[ci];
            failed[ci] += reports[i].cls.failed[ci];
        }
    }
    printf("\n%-20s %10s %10s %10s\n", "operation class", "operations", "cut runs", "failed");
    for (int ci = 0; ci < CL_COUNT; ci++) {
        if (ops[ci] != 0) {
            printf("%-20s %10u %10u %10u\n", class_name[ci], ops[ci], runs[ci], failed[ci]);
        }
    }
    if (n_failures != 0) {
        printf("\nfirst %u of %u failing cases:\n", n_failures < MAX_FAILURES ? n_failures : MAX_FAILURES, n_failures);
        for (unsigned i = 0; i < n_failures && i < MAX_FAILURES; i++) {
            printf("  %s\n", failures[i].text);
        }
    }
    return c.halts + t.halts + c.lost_revert + t.lost_revert + c.no_dfu + t.no_dfu + c.asserts + t.asserts + c.not_converged + t.not_converged
           + c.inconsistent + t.inconsistent;
}

static const scenario_t scenarios[] = {
    #if MBOOT_POLICY_SINGLE
    // One slot, written in place: nothing is pending and there is nothing to revert or confirm.
    {"DFU session", SK_DFU, &st.v1, c_dfu_install, 2},
    {"first boot", SK_INIT, &st.factory, c_boot, 0},
    #elif MBOOT_POLICY_OVERWRITE_EXTERNAL
    {"copy", SK_SWAP, &st.pending, c_boot, 0},
    {"set pending", SK_PENDING, &st.written, c_set_pending, 0},
    {"DFU session", SK_DFU, &st.v1, c_dfu_install, 2},
    {"first boot", SK_INIT, &st.factory, c_boot, 0},
    #else
    {"swap", SK_SWAP, &st.pending, c_boot, 0},
    {"revert", SK_REVERT, &st.swapped, c_boot, 0},
    {"confirm", SK_CONFIRM, &st.swapped, c_confirm_boot, 0},
    {"set pending", SK_PENDING, &st.written, c_set_pending, 0},
    {"DFU session", SK_DFU, &st.v1, c_dfu_install, 2},
    {"first boot", SK_INIT, &st.factory, c_boot, 0},
    #endif
};

static void sweep_set(const char *only, bool clean, bool torn) {
    for (size_t i = 0; i < sizeof(scenarios) / sizeof(scenarios[0]); i++) {
        if (only != NULL && strstr(scenarios[i].name, only) == NULL) {
            continue;
        }
        sweep(&scenarios[i], clean, torn);
    }
}



// Two cuts in a row: a torn cut at operation k1 of the scenario, then a torn cut at operation
// k2 of the first boot that follows. A word torn twice can lose a value that has only one shadow
// copy, so this shows what a repeated power failure does.
static void sweep_double(const scenario_t *s, int k1_stride, int k2_stride) {
    if (n_reports >= MAX_REPORTS) {
        return;
    }
    scn_report_t *rep = &reports[n_reports++];
    memset(rep, 0, sizeof(*rep));
    snprintf(rep->label, sizeof(rep->label), "%s x2 %s", s->name, size_tag);

    fake_flash_restore(*s->start);
    install_blob = images[s->install_version];
    run_child(s->action);
    uint32_t m = fake_flash_op_count();
    fake_flash_op_t *trace = malloc(m * sizeof(*trace));
    memcpy(trace, fake_flash_trace(), m * sizeof(*trace));
    rep->ops = m;
    uint32_t wu = mboot_devs[0].write_unit;
    clock_t t0 = clock();
    unsigned runs = 0;

    for (uint32_t k1 = 1; k1 <= m; k1 += (uint32_t)k1_stride) {
        const fake_flash_op_t *op1 = &trace[k1 - 1];
        variant_t v1 = {opts.double_clean ? FAKE_FLASH_CUT_BEFORE : FAKE_FLASH_CUT_DURING, op1->kind == FAKE_FLASH_OP_WRITE && op1->len == wu ? 128u : 192u, 1};
        fake_flash_restore(*s->start);
        fake_flash_arm(k1, v1.mode, v1.q8, v1.seed);
        install_blob = images[s->install_version];
        run_t r = run_child(s->action);
        fake_flash_disarm();
        if (!r.cut) {
            continue;
        }
        fake_flash_snapshot_t *s1 = fake_flash_snapshot();
        run_t b = run_child(c_boot);
        uint32_t m2 = b.ops;
        for (uint32_t k2 = 1; k2 <= m2; k2 += (uint32_t)k2_stride) {
            fake_flash_restore(s1);
            fake_flash_arm(k2, opts.double_clean ? FAKE_FLASH_CUT_BEFORE : FAKE_FLASH_CUT_DURING, 128, 2);
            run_t r2 = run_child(c_boot);
            fake_flash_disarm();
            verdict_t v;
            if (r2.hang || r2.crash) {
                memset(&v, 0, sizeof(v));
                v.halt = true;
                snprintf(v.what, sizeof(v.what), "%s", describe(&r2));
            } else {
                v = settle_and_judge(s, opts.stride_dfu != 0 && (runs % (unsigned)opts.stride_dfu) == 0);
            }
            runs++;
            tally_add(&rep->torn, &v);
            op_class_t c = classify(op1);
            rep->cls.runs[c]++;
            if (verdict_failed(&v)) {
                rep->cls.failed[c]++;
                char what[300];
                snprintf(what, sizeof(what), "first cut on %s %s, second cut at op %u of the next boot: %s", class_name[c],
                    op1->kind == FAKE_FLASH_OP_ERASE ? "erase" : "write", k2, v.what);
                note_failure(s->name, "torn x2", k1, mode_name(&v1), what);
            }
        }
        fake_flash_snapshot_free(s1);
    }
    for (uint32_t i = 0; i < m; i++) {
        rep->cls.ops[classify(&trace[i])]++;
    }
    free(trace);
    printf("  %-28s %4u operations, %5u runs, %.1f s\n", rep->label, m, rep->torn.runs, (double)(clock() - t0) / CLOCKS_PER_SEC);
    fflush(stdout);
}

static void sweep_double_set(const char *only, int k1_stride, int k2_stride) {
    for (size_t i = 0; i < sizeof(scenarios) / sizeof(scenarios[0]); i++) {
        if (only != NULL && strstr(scenarios[i].name, only) == NULL) {
            continue;
        }
        if (scenarios[i].kind == SK_SWAP || scenarios[i].kind == SK_REVERT || scenarios[i].kind == SK_DFU) {
            sweep_double(&scenarios[i], k1_stride, k2_stride);
        }
    }
}

// One cut case with the log of every boot printed, for debugging a failing case of a sweep.
static void run_single_case(const char *name, uint32_t k, int mode, uint32_t q8, uint32_t seed) {
    const scenario_t *s = NULL;
    for (size_t i = 0; i < sizeof(scenarios) / sizeof(scenarios[0]); i++) {
        if (strstr(scenarios[i].name, name) != NULL) {
            s = &scenarios[i];
            break;
        }
    }
    if (s == NULL) {
        printf("no scenario matches '%s'\n", name);
        return;
    }
    core_shared->log_echo = true;
    fake_flash_restore(*s->start);
    fake_flash_arm(k, (fake_flash_cut_mode_t)mode, q8, seed);
    install_blob = images[s->install_version];
    printf("--- cut run (%s, op %u, mode %d, q %u, seed %u)\n", s->name, k, mode, q8, seed);
    run_t r = run_child(s->action);
    fake_flash_disarm();
    printf("--- %s, %u unreadable words in the flash\n", describe(&r), fake_flash_count_ecc_invalid(0, 0, mboot_devs[0].size));
    for (int i = 0; i < 4; i++) {
        printf("--- boot %d\n", i + 1);
        r = run_child(c_boot);
        printf("--- %s\n", describe(&r));
    }
}

// Two torn cuts with the log of each boot printed.
static void run_single_case2(const char *name, uint32_t k1, uint32_t q1, uint32_t seed1, uint32_t k2, uint32_t q2, uint32_t seed2) {
    const scenario_t *s = NULL;
    for (size_t i = 0; i < sizeof(scenarios) / sizeof(scenarios[0]); i++) {
        if (strstr(scenarios[i].name, name) != NULL) {
            s = &scenarios[i];
            break;
        }
    }
    if (s == NULL) {
        printf("no scenario matches '%s'\n", name);
        return;
    }
    core_shared->log_echo = true;
    fake_flash_restore(*s->start);
    install_blob = images[s->install_version];
    fake_flash_arm(k1, opts.double_clean ? FAKE_FLASH_CUT_BEFORE : FAKE_FLASH_CUT_DURING, q1, seed1);
    printf("--- first cut (%s, op %u, q %u, seed %u)\n", s->name, k1, q1, seed1);
    run_t r = run_child(s->action);
    fake_flash_disarm();
    printf("--- %s, %u unreadable words in the flash\n", describe(&r), fake_flash_count_ecc_invalid(0, 0, mboot_devs[0].size));
    fake_flash_counter_reset();
    fake_flash_arm(k2, opts.double_clean ? FAKE_FLASH_CUT_BEFORE : FAKE_FLASH_CUT_DURING, q2, seed2);
    printf("--- second cut at op %u of the next boot (q %u, seed %u)\n", k2, q2, seed2);
    r = run_child(c_boot);
    fake_flash_disarm();
    printf("--- %s, %u unreadable words in the flash\n", describe(&r), fake_flash_count_ecc_invalid(0, 0, mboot_devs[0].size));
    for (int i = 0; i < 2; i++) {
        printf("--- boot %d\n", i + 1);
        r = run_child(c_boot);
        printf("--- %s\n", describe(&r));
    }
    printf("--- recovery: erase the primary trailer and header, DFU install of v3\n");
    r = run_child(c_recover);
    printf("--- %s\n", describe(&r));
    install_blob = images[3];
    r = run_child(c_dfu_install);
    printf("--- %s\n", describe(&r));
    for (int i = 0; i < 3; i++) {
        printf("--- boot %d\n", i + 1);
        r = run_child(c_boot);
        printf("--- %s\n", describe(&r));
    }
}

// ---- main ----

static void usage(void) {
    fprintf(stderr,
        "usage: core_test --images DIR [options] command...\n"
        "commands: unit functional fi swap revert torn double sweeps all\n"
        "  unit        unit tests of printf, crc, flash map, shadow words, log, counter, request\n"
        "  functional  good swap, confirm, revert, tampered update, refused downgrade\n"
        "  fi          fault injection counter and hook against fake flash cuts\n"
        "  swap        clean power cut loop of the swap scenario over every operation (before / between / after)\n"
        "  revert      the same for the revert scenario\n"
        "  torn        torn word sweep over every operation of every scenario\n"
        "  double      two torn cuts in a row (--k1-stride, --k2-stride; --double-clean for clean cuts) for swap, revert and DFU\n"
        "  sweeps      clean and torn sweeps of every scenario\n"
        "  --case2 SCENARIO K1 Q1 SEED1 K2 Q2 SEED2   two torn cuts, the second in the next boot\n"
        "  --case SCENARIO K MODE Q SEED   one cut with the log of each boot (mode 0 before, 1 after, 2 during, 3 between)\n"
        "options: --sizes 3s,15s  --kstride N  --dfu-stride N  --torn-variants 1..5  --scenario NAME\n"
        "         --stock (flash models ECC although the glue has no ECC policy)  --verbose  --trace\n");
}

int main(int argc, char **argv) {
    const char *sizes = "3s,15s";
    const char *only = NULL;
    char **case_args = NULL;
    char **case2_args = NULL;
    const char *cmds[16];
    int ncmds = 0;
    for (int i = 1; i < argc; i++) {
        if (strcmp(argv[i], "--images") == 0 && i + 1 < argc) {
            img_dir = argv[++i];
        } else if (strcmp(argv[i], "--sizes") == 0 && i + 1 < argc) {
            sizes = argv[++i];
        } else if (strcmp(argv[i], "--kstride") == 0 && i + 1 < argc) {
            opts.stride_k = atoi(argv[++i]);
        } else if (strcmp(argv[i], "--dfu-stride") == 0 && i + 1 < argc) {
            opts.stride_dfu = atoi(argv[++i]);
        } else if (strcmp(argv[i], "--torn-variants") == 0 && i + 1 < argc) {
            opts.torn_variants = atoi(argv[++i]);
        } else if (strcmp(argv[i], "--scenario") == 0 && i + 1 < argc) {
            only = argv[++i];
        } else if (strcmp(argv[i], "--stock") == 0) {
            core_force_ecc = true;
        } else if (strcmp(argv[i], "--k1-stride") == 0 && i + 1 < argc) {
            opts.stride_k1 = atoi(argv[++i]);
        } else if (strcmp(argv[i], "--k2-stride") == 0 && i + 1 < argc) {
            opts.stride_k2 = atoi(argv[++i]);
        } else if (strcmp(argv[i], "--double-clean") == 0) {
            opts.double_clean = true;
        } else if (strcmp(argv[i], "--list") == 0) {
            opts.list_cases = true;
        } else if (strcmp(argv[i], "--trace") == 0) {
            opts.show_trace = true;
        } else if (strcmp(argv[i], "--verbose") == 0) {
            opts.verbose = true;
        } else if (strcmp(argv[i], "--case2") == 0 && i + 7 < argc) {
            case2_args = &argv[i + 1];
            i += 7;
        } else if (strcmp(argv[i], "--case") == 0 && i + 5 < argc) {
            case_args = &argv[i + 1];
            i += 5;
        } else if (argv[i][0] != '-' && ncmds < 16) {
            cmds[ncmds++] = argv[i];
        } else {
            usage();
            return 2;
        }
    }
    if (case2_args != NULL) {
        core_port_init();
        if (!load_images(sizes) || !build_states()) {
            return 1;
        }
        run_single_case2(case2_args[0], (uint32_t)atoi(case2_args[1]), (uint32_t)atoi(case2_args[2]), (uint32_t)atoi(case2_args[3]),
            (uint32_t)atoi(case2_args[4]), (uint32_t)atoi(case2_args[5]), (uint32_t)atoi(case2_args[6]));
        return 0;
    }
    if (case_args != NULL) {
        core_port_init();
        if (!load_images(sizes) || !build_states()) {
            return 1;
        }
        run_single_case(case_args[0], (uint32_t)atoi(case_args[1]), atoi(case_args[2]), (uint32_t)atoi(case_args[3]), (uint32_t)atoi(case_args[4]));
        return 0;
    }
    if (ncmds == 0 || opts.stride_k < 1 || opts.torn_variants < 1 || opts.torn_variants > 5) {
        usage();
        return 2;
    }
    core_port_init();
    core_shared->log_echo = opts.verbose;

    bool do_unit = false, do_func = false, do_clean = false, do_torn = false, do_double = false, do_fi = false;
    bool want_swap = false, want_revert = false, has_sweeps = false;
    for (int i = 0; i < ncmds; i++) {
        if (strcmp(cmds[i], "unit") == 0 || strcmp(cmds[i], "all") == 0) {
            do_unit = true;
        }
        if (strcmp(cmds[i], "functional") == 0 || strcmp(cmds[i], "all") == 0) {
            do_func = true;
        }
        if (strcmp(cmds[i], "swap") == 0) {
            do_clean = true;
            want_swap = true;
        }
        if (strcmp(cmds[i], "revert") == 0) {
            do_clean = true;
            want_revert = true;
        }
        if (strcmp(cmds[i], "torn") == 0) {
            do_torn = true;
        }
        if (strcmp(cmds[i], "double") == 0) {
            do_double = true;
        }
        if (strcmp(cmds[i], "fi") == 0 || strcmp(cmds[i], "all") == 0) {
            do_fi = true;
        }
        if (strcmp(cmds[i], "sweeps") == 0 || strcmp(cmds[i], "all") == 0) {
            do_clean = true;
            do_torn = true;
            has_sweeps = true;
        }
    }

    int exit_code = 0;
    if (do_unit) {
        exit_code |= core_unit_run() != 0;
    }

    char sizes_buf[64];
    snprintf(sizes_buf, sizeof(sizes_buf), "%s", sizes);
    for (char *tag = strtok(sizes_buf, ","); tag != NULL && (do_func || do_clean || do_torn || do_double || do_fi); tag = strtok(NULL, ",")) {
        if (!load_images(tag) || !build_states()) {
            exit_code = 1;
            break;
        }
        if (do_func) {
            core_failures = 0;
            test_functional();
            printf("functional %s: %s\n", size_tag, core_failures == 0 ? "PASS" : "FAIL");
            exit_code |= core_failures != 0;
            // the functional tests replace the states; rebuild for the sweeps
            if (!build_states()) {
                exit_code = 1;
                break;
            }
        }
        if (do_func) {
            core_failures = 0;
            test_no_image_recovery();
            exit_code |= core_failures != 0;
        }
        if (do_fi) {
            core_failures = 0;
            test_fi_equivalence();
            exit_code |= core_failures != 0;
        }
        if (do_double) {
            sweep_double_set(only, opts.stride_k1, opts.stride_k2);
        }
        if (do_clean || do_torn) {
            if ((want_swap || want_revert) && !do_torn && !has_sweeps) {
                if (want_swap) {
                    sweep_set("swap", true, false);
                }
                if (want_revert) {
                    sweep_set("revert", true, false);
                }
            } else {
                sweep_set(only, do_clean, do_torn);
            }
        }
    }
    if (n_reports != 0) {
        unsigned bad = print_report(core_force_ecc && !do_unit ? "control: torn word sweep without the ECC policy" : "power cut sweeps");
        if (!core_force_ecc) {
            exit_code |= bad != 0;
        }
    }
    printf("\nresult: %s\n", exit_code == 0 ? "PASS" : "FAIL");
    return exit_code;
}
