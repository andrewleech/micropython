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

#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "bootutil/bootutil_public.h"
#include "flash_map_backend/flash_map_backend.h"
#include "host_env.h"
#include "mboot_fsload.h"
#include "mboot_request.h"
#include "mboot_updatelog.h"
#include "mboot_validate.h"
#include "sysflash/sysflash.h"

// Host tests of fsload, over the real flash map backend, update audit log and bootutil validation:
// files that must be installed, files and requests that must be refused without a flash write,
// and the retry of the single policy after a power cut.
//
//   test_fsload <testdata dir>
//
// <testdata dir>/cases.txt (written by make_testdata.py) has one case per line:
//
//   <name> <expected> <device file> <element file> <image file or ->
//
// <expected> is "ok" or a comma separated list of result codes, any of which is accepted. The
// device file becomes the contents of device 0 (address 0x90000000) and the element file is the
// request. After a case that must succeed the target slot must hold the image file. After a
// case that must fail: no write or erase outside the update audit log area, slots unchanged, and a
// FSLOAD_FAILED record with the result code.
//
// With the swap policy a success is also checked for the pending test swap
// (boot_swap_type_multi); with the single policy the primary slot holds the image.
//
// The power cut sweep runs the first case that must succeed with the flash dying after every
// possible number of write and erase calls. It restarts as the bootloader does (intent record,
// request again) and requires the result to be a slot that validates.

#define MAX_FILE (8u << 20)

static int failures;
static int checks;

// Replaces the default store of the result word of a STATUS element, which would write to an
// address of the target.
static unsigned status_stores;
static uint32_t status_addr, status_value;

void mboot_fsload_status_store(uint32_t addr, uint32_t value) {
    status_stores++;
    status_addr = addr;
    status_value = value;
}

#define CHECK(cond, ...) \
    do { \
        checks++; \
        if (!(cond)) { \
            failures++; \
            printf("  FAIL %s:%d: %s: ", __FILE__, __LINE__, #cond); \
            printf(__VA_ARGS__); \
            printf("\n"); \
        } \
    } while (0)

static uint8_t *read_file(const char *dir, const char *name, size_t *len) {
    char path[512];
    snprintf(path, sizeof(path), "%s/%s", dir, name);
    FILE *f = fopen(path, "rb");
    if (f == NULL) {
        fprintf(stderr, "cannot open %s\n", path);
        exit(2);
    }
    fseek(f, 0, SEEK_END);
    long n = ftell(f);
    fseek(f, 0, SEEK_SET);
    uint8_t *buf = malloc(n ? n : 1);
    if (buf == NULL || fread(buf, 1, n, f) != (size_t)n || (size_t)n > MAX_FILE) {
        fprintf(stderr, "cannot read %s\n", path);
        exit(2);
    }
    fclose(f);
    *len = n;
    return buf;
}

static const char *code_name(int code) {
    switch (code) {
        case MBOOT_RES_OK:
            return "OK";
        case MBOOT_RES_ERR_FLASH:
            return "FLASH";
        case MBOOT_RES_ERR_HEADER:
            return "HEADER";
        case MBOOT_RES_ERR_HASH:
            return "HASH";
        case MBOOT_RES_ERR_SIG:
            return "SIG";
        case MBOOT_RES_ERR_DOWNGRADE:
            return "DOWNGRADE";
        case MBOOT_RES_ERR_TOO_BIG:
            return "TOO_BIG";
        case MBOOT_RES_ERR_NOT_TARGET:
            return "NOT_TARGET";
        case MBOOT_RES_ERR_PENDING:
            return "PENDING";
        case MBOOT_RES_ERR_LAYOUT:
            return "LAYOUT";
        case MBOOT_RES_ERR_NO_IMAGE:
            return "NO_IMAGE";
        case MBOOT_RES_ERR_REQUEST:
            return "REQUEST";
        case MBOOT_RES_ERR_FS_MOUNT:
            return "FS_MOUNT";
        case MBOOT_RES_ERR_FS_OPEN:
            return "FS_OPEN";
        case MBOOT_RES_ERR_FS_READ:
            return "FS_READ";
        case MBOOT_RES_ERR_FS_GZIP:
            return "FS_GZIP";
        case MBOOT_RES_ERR_FS_PATH:
            return "FS_PATH";
        default:
            return "?";
    }
}

static bool expected_has(const char *list, int code) {
    char buf[128];
    snprintf(buf, sizeof(buf), "%s", list);
    char *save;
    for (char *tok = strtok_r(buf, ",", &save); tok != NULL; tok = strtok_r(NULL, ",", &save)) {
        if (strcmp(tok, "ok") == 0 ? code == MBOOT_RES_OK : atoi(tok) == code) {
            return true;
        }
    }
    return false;
}

// Offset of the image in the target slot.
static uint32_t target_off(void) {
    #if defined(FUZZ_POLICY_SINGLE)
    return HOST_PRIMARY_OFF;
    #else
    return HOST_SECONDARY_OFF + HOST_ERASE;
    #endif
}

static uint32_t le32(const uint8_t *p) {
    return p[0] | (uint32_t)p[1] << 8 | (uint32_t)p[2] << 16 | (uint32_t)p[3] << 24;
}

static uint16_t le16(const uint8_t *p) {
    return p[0] | (uint16_t)p[1] << 8;
}

// Length of the image in buf: header, image, protected and unprotected TLVs.
static uint32_t image_len(const uint8_t *img) {
    uint32_t off = le16(img + 8) + le32(img + 12) + le16(img + 10);
    return off + le16(img + off + 2);
}

// Copy of the slots, for the "slots unchanged" checks (the update audit log area is excluded).
static uint8_t snapshot[HOST_SLOT_SIZE];

static void snapshot_slots(void) {
    memcpy(snapshot, host_slots(), HOST_SLOT_SIZE);
}

static bool slots_unchanged(void) {
    return memcmp(snapshot, host_slots(), HOST_LOG_OFF) == 0 &&
           memcmp(snapshot + HOST_LOG_OFF + HOST_LOG_SIZE, host_slots() + HOST_LOG_OFF + HOST_LOG_SIZE,
        HOST_SLOT_SIZE - HOST_LOG_OFF - HOST_LOG_SIZE) == 0;
}

// The image in area_id validates (the old image, for the primary slot under the single policy).
static bool slot_validates(uint32_t area_id) {
    const struct flash_area *fa;
    if (flash_area_open(area_id, &fa) != 0) {
        return false;
    }
    mboot_validate_result_t r = mboot_validate_view(fa, VALIDATE_FULL | VALIDATE_CHECK_TARGET);
    return r.code == MBOOT_RES_OK;
}

static void install_initial(const uint8_t *img, size_t len) {
    // Initial image in the primary slot, written as bootutil expects for a confirmed image
    // (neither the single policy nor a swap request needs the trailer).
    const struct flash_area *fa;
    CHECK(flash_area_open(FLASH_AREA_IMAGE_PRIMARY(0), &fa) == 0, "open primary");
    size_t padded = (len + 15) / 16 * 16;
    uint8_t *buf = malloc(padded);
    memset(buf, 0xff, padded);
    memcpy(buf, img, len);
    CHECK(flash_area_erase(fa, 0, (len + HOST_ERASE - 1) / HOST_ERASE * HOST_ERASE) == 0, "erase primary");
    CHECK(flash_area_write(fa, 0, buf, padded) == 0, "write primary");
    free(buf);
}

#if defined(TRACE_STREAM)
void trace_reset(void);
void trace_report(const char *name);
#endif

typedef struct {
    char name[64];
    char expected[64];
    char dev_file[128];
    char elems_file[128];
    char image_file[128];
} test_case_t;

static int run_case(const char *dir, const test_case_t *tc, const uint8_t *initial, size_t initial_len) {
    size_t dev_len, elems_len, img_len = 0;
    uint8_t *dev = read_file(dir, tc->dev_file, &dev_len);
    uint8_t *elems = read_file(dir, tc->elems_file, &elems_len);
    uint8_t *img = strcmp(tc->image_file, "-") != 0 ? read_file(dir, tc->image_file, &img_len) : NULL;

    host_fs_set(dev, dev_len);
    host_slots_reset();
    #if defined(FUZZ_POLICY_SINGLE)
    install_initial(initial, initial_len);
    #else
    (void)initial;
    (void)initial_len;
    #endif
    host_flash_stats_clear();
    snapshot_slots();
    status_stores = 0;
    int before_failures = failures;

    #if defined(TRACE_STREAM)
    trace_reset();
    #endif
    int r = mboot_fsload_run(elems, elems_len);
    #if defined(TRACE_STREAM)
    if (strcmp(tc->name, "install_fat16") == 0 || strcmp(tc->name, "install_gzip_raw") == 0 || strcmp(tc->name, "refused_bad_sig") == 0) {
        trace_report(tc->name);
    }
    #endif
    const host_flash_stats_t *st = host_flash_stats();

    mboot_log_rec_t rec;
    CHECK(mboot_updatelog_read(0, &rec) == 0, "log has a record");
    CHECK(expected_has(tc->expected, r), "result %s (%d), expected %s", code_name(r), r, tc->expected);
    CHECK(st->violations == 0, "flash model violations %u", (unsigned)st->violations);

    if (r == MBOOT_RES_OK) {
        CHECK(rec.type == LOG_FSLOAD_DONE && rec.result == 0, "log type %u result %u", rec.type, rec.result);
        if (img != NULL) {
            uint32_t len = image_len(img);
            CHECK(len <= img_len, "image length");
            CHECK(memcmp(host_slots() + target_off(), img, len) == 0, "slot holds the image");
        }
        #if !defined(FUZZ_POLICY_SINGLE)
        CHECK(boot_swap_type_multi(0) == BOOT_SWAP_TYPE_TEST, "pending test swap, got %d", boot_swap_type_multi(0));
        #endif
    } else {
        CHECK(rec.type == LOG_FSLOAD_FAILED && rec.result == r, "log type %u result %u", rec.type, rec.result);
        CHECK(st->writes_outside_log == 0 && st->erases_outside_log == 0, "flash writes %u erases %u outside the log",
            (unsigned)st->writes_outside_log, (unsigned)st->erases_outside_log);
        CHECK(slots_unchanged(), "slots changed");
    }

    if (strcmp(tc->name, "install_status") == 0) {
        CHECK(status_stores == 1 && status_addr == 0x20001000 && status_value == 0, "status stores %u value %u", status_stores, status_value);
    } else if (strcmp(tc->name, "refused_status_failure") == 0) {
        CHECK(status_stores == 1 && status_addr == 0x20001000 && status_value == (uint32_t)-r, "status stores %u value %u", status_stores, status_value);
    } else {
        CHECK(status_stores == 0, "unexpected status store");
    }

    const mboot_fsload_stream_stats_t *ss = mboot_fsload_stream_stats();
    printf("%-40s %-10s %s  reads %u hits %u rewinds %u discarded %u  dev0 %llu B\n", tc->name,
        code_name(r), failures == before_failures ? "pass" : "FAIL", (unsigned)ss->reads, (unsigned)ss->cache_hits,
        (unsigned)ss->rewinds, (unsigned)ss->discarded, (unsigned long long)st->fs_read_bytes);

    free(dev);
    free(elems);
    free(img);
    return 0;
}

// Runs the request, restarts like boot_main.c after a power cut and checks the slots.
static void power_cut_sweep(const char *dir, const test_case_t *tc, const uint8_t *initial, size_t initial_len) {
    size_t dev_len, elems_len, img_len;
    uint8_t *dev = read_file(dir, tc->dev_file, &dev_len);
    uint8_t *elems = read_file(dir, tc->elems_file, &elems_len);
    uint8_t *img = read_file(dir, tc->image_file, &img_len);

    // Operations of an uncut run.
    host_fs_set(dev, dev_len);
    host_slots_reset();
    #if defined(FUZZ_POLICY_SINGLE)
    install_initial(initial, initial_len);
    #endif
    host_flash_cut_after(UINT32_MAX);
    CHECK(mboot_fsload_run(elems, elems_len) == MBOOT_RES_OK, "uncut run");
    uint32_t total = host_flash_ops();
    host_flash_power_on();

    unsigned retried = 0, intact = 0, completed = 0;
    for (uint32_t k = 0; k <= total; ++k) {
        host_slots_reset();
        #if defined(FUZZ_POLICY_SINGLE)
        install_initial(initial, initial_len);
        #endif
        host_flash_cut_after(k);
        mboot_fsload_run(elems, elems_len);
        host_flash_power_on();

        // Reset: the request in RAM is gone.
        mboot_request_t req;
        memset(&req, 0, sizeof(req));
        if (mboot_intent_load(&req)) {
            retried++;
            CHECK(mboot_fsload_run(req.elems, req.elems_len) == MBOOT_RES_OK, "retry after cut at %u", (unsigned)k);
        }
        #if defined(FUZZ_POLICY_SINGLE)
        // The primary slot holds the old or the new image, never a partial one.
        bool ok = slot_validates(FLASH_AREA_IMAGE_PRIMARY(0));
        CHECK(ok, "primary slot validates after a cut at operation %u", (unsigned)k);
        if (memcmp(host_slots() + HOST_PRIMARY_OFF, img, image_len(img)) == 0) {
            completed++;
        } else {
            intact++;
        }
        #else
        // The primary slot is never written; the secondary slot is incomplete until a second
        // request has completed it.
        CHECK(host_flash_stats()->violations == 0, "violations after a cut at %u", (unsigned)k);
        host_flash_stats_clear();
        host_flash_power_on();
        CHECK(mboot_fsload_run(elems, elems_len) == MBOOT_RES_OK, "second request after a cut at %u", (unsigned)k);
        CHECK(memcmp(host_slots() + target_off(), img, image_len(img)) == 0, "secondary holds the image after a cut at %u", (unsigned)k);
        CHECK(boot_swap_type_multi(0) == BOOT_SWAP_TYPE_TEST, "pending after a cut at %u", (unsigned)k);
        CHECK(host_flash_stats()->writes_outside_log > 0, "second request wrote");
        completed++;
        #endif
    }
    printf("power cut sweep over %u operations: %u completed, %u old image kept, %u retried from the intent record\n",
        (unsigned)total, completed, intact, retried);

    #if defined(FUZZ_POLICY_SINGLE)
    // The file is gone when the retry runs. Pass 1 fails, the intent record is erased, and
    // nothing is retried at the next reset (a bad file cannot cause a retry loop).
    unsigned vanished = 0;
    for (uint32_t k = 1; k <= total; ++k) {
        host_slots_reset();
        install_initial(initial, initial_len);
        host_flash_cut_after(k);
        mboot_fsload_run(elems, elems_len);
        host_flash_power_on();

        mboot_request_t req;
        memset(&req, 0, sizeof(req));
        if (!mboot_intent_load(&req)) {
            continue;
        }
        vanished++;
        host_fs_set(dev, 0);
        int r = mboot_fsload_run(req.elems, req.elems_len);
        CHECK(r != MBOOT_RES_OK, "retry with the file deleted fails (cut at %u)", (unsigned)k);
        CHECK(!mboot_intent_load(&req), "intent record erased after a failed retry (cut at %u)", (unsigned)k);
        host_fs_set(dev, dev_len);
    }
    printf("retry with the file deleted: %u cuts left an intent record, every retry failed in pass 1 and erased it\n", vanished);
    CHECK(vanished > 0, "some cut left an intent record");
    #endif
    free(dev);
    free(elems);
    free(img);
}

#if defined(FUZZ_POLICY_SINGLE)
// Pass 2 copies the file again after pass 1 validated it. If the source reads back differently
// the second time, the slot does not validate: the load fails and the retry record stays, so the
// next reset loads the file again.
static void single_copy_mismatch(const char *dir, const test_case_t *tc, const uint8_t *initial, size_t initial_len) {
    size_t dev_len, elems_len;
    uint8_t *dev = read_file(dir, tc->dev_file, &dev_len);
    uint8_t *elems = read_file(dir, tc->elems_file, &elems_len);

    host_fs_set(dev, dev_len);
    host_slots_reset();
    install_initial(initial, initial_len);
    host_flash_stats_clear();
    CHECK(mboot_fsload_run(elems, elems_len) == MBOOT_RES_OK, "uncut run");
    uint32_t reads = host_flash_stats()->fs_reads;

    unsigned mismatches = 0;
    for (uint32_t n = reads - 8; n <= reads; ++n) {
        host_slots_reset();
        install_initial(initial, initial_len);
        host_fs_set(dev, dev_len);
        host_flash_stats_clear();
        host_fs_corrupt_read(n);
        int r = mboot_fsload_run(elems, elems_len);
        host_fs_corrupt_read(0);
        if (r == MBOOT_RES_OK) {
            // The flipped byte was not part of the copy (a read of the end of the file).
            continue;
        }
        mismatches++;
        mboot_request_t req;
        memset(&req, 0, sizeof(req));
        CHECK(mboot_intent_load(&req), "intent record kept after a copy mismatch (read %u), result %d", (unsigned)n, r);
        CHECK(mboot_fsload_run(req.elems, req.elems_len) == MBOOT_RES_OK, "retry after a copy mismatch (read %u)", (unsigned)n);
        CHECK(!mboot_intent_load(&req), "intent record erased after the retry succeeded (read %u)", (unsigned)n);
    }
    printf("copy mismatch in pass 2: %u of 9 corrupted reads were part of the copy; intent kept, retry loaded the image\n", mismatches);
    CHECK(mismatches > 0, "some corrupted read changed the copy");
    free(dev);
    free(elems);
}
#endif

int main(int argc, char **argv) {
    setvbuf(stdout, NULL, _IOLBF, 0);
    if (argc < 2) {
        fprintf(stderr, "usage: %s <testdata dir>\n", argv[0]);
        return 2;
    }
    const char *dir = argv[1];
    size_t initial_len;
    uint8_t *initial = read_file(dir, "initial.bin", &initial_len);

    size_t manifest_len;
    char *manifest = (char *)read_file(dir, "cases.txt", &manifest_len);
    manifest = realloc(manifest, manifest_len + 1);
    manifest[manifest_len] = '\0';

    test_case_t first_ok;
    bool have_first_ok = false;
    int cases = 0;
    char *save;
    for (char *line = strtok_r(manifest, "\n", &save); line != NULL; line = strtok_r(NULL, "\n", &save)) {
        test_case_t tc;
        if (line[0] == '#' || line[0] == '\0') {
            continue;
        }
        if (sscanf(line, "%63s %63s %127s %127s %127s", tc.name, tc.expected, tc.dev_file, tc.elems_file, tc.image_file) != 5) {
            fprintf(stderr, "bad manifest line: %s\n", line);
            return 2;
        }
        run_case(dir, &tc, initial, initial_len);
        cases++;
        if (!have_first_ok && strcmp(tc.expected, "ok") == 0 && strcmp(tc.image_file, "-") != 0) {
            first_ok = tc;
            have_first_ok = true;
        }
    }
    if (have_first_ok) {
        printf("power cut sweep, case %s\n", first_ok.name);
        power_cut_sweep(dir, &first_ok, initial, initial_len);
        #if defined(FUZZ_POLICY_SINGLE)
        single_copy_mismatch(dir, &first_ok, initial, initial_len);
        #endif
    }
    printf("%d cases, %d checks, %d failed\n", cases, checks, failures);
    free(manifest);
    free(initial);
    return failures != 0;
}
