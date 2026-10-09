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
#include <inttypes.h>
#include <stdarg.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/wait.h>
#include <unistd.h>

#include "bootutil/bootutil.h"
#include "bootutil/bootutil_public.h"
#include "bootutil/fault_injection_hardening.h"
#include "bootutil/image.h"
#include "flash_map_backend/flash_map_backend.h"
#include "sysflash/sysflash.h"

#include "fake_flash.h"
#include "glue/host_glue.h"
#if defined(HOST_SHARED_GLUE)
#include "spi_nor/spi_nor.h"
#endif

// Power-cut sweep over bootutil on the fake flash.
//
// One simulated boot attempt is one forked process that runs boot_go() and,
// for the confirming scenarios, the app-side boot_set_confirmed(). A cut ends
// the process at the injected flash operation; the flash contents live in
// shared memory and carry over. After the cut the device is "reset" and boots
// again, without injection, until a boot performs no flash operation (settled)
// or the boot limit is reached. Results are written as JSON lines for
// tests/mboot/host/sweep.py, which labels the operations with phases.py.

#define MAX_TRACE (4096)
#define MAX_TEARS (16)
#define MAX_DEVS FAKE_FLASH_MAX_DEVS

typedef enum {
    SCN_SWAP,           // test swap, app never confirms: settles on the reverted old image
    SCN_SWAP_CONFIRM,   // test swap, app confirms the new image
    SCN_REVERT,         // new image running unconfirmed, boot performs the revert
    SCN_PERM,           // permanent swap (secondary image carries image_ok)
    SCN_INSTALL,        // single slot policy: the new image is written over the only slot, then boot
} scenario_t;

#define N_SCENARIOS 5

static const char *const scenario_names[] = {"swap", "swap_confirm", "revert", "perm", "install"};

typedef enum {
    ATT_DONE,
    ATT_CUT,
    ATT_HALT,
    ATT_CRASH,
} att_end_t;

typedef struct {
    int magic;
    int copy_done;
    int image_ok;
    int swap_type;
    int rc;
} slot_state_t;

typedef struct {
    int boot_ok;
    int image_major;
    uint32_t ops;
    int confirm_rc;
    slot_state_t pri;
    slot_state_t sec;
    int swap_type;
    char halt_file[64];
    int halt_line;
} att_res_t;

typedef struct {
    att_end_t end;
    int raw_status;
    int boot_ok;
    int image;          // 1 = old image bytes, 2 = new image bytes, 0 = neither
    uint32_t ops;
    slot_state_t pri;
    slot_state_t sec;
    int swap_type;
    int halt_line;
    char halt_file[64];
} boot_info_t;

typedef struct {
    uint32_t seq;
    int kind;
    int dev;
    uint32_t off;
    uint32_t len;
} trace_op_t;

typedef struct {
    bool cut;
    const char *outcome;
    int boots;
    int final_image;
    bool saw_new;
    boot_info_t last;
    uint32_t violations;
    uint32_t overprograms;
    uint32_t ecc_events;
    char note[160];
} trial_t;

static struct {
    unsigned n_devs;
    fake_flash_dev_cfg_t devs[MAX_DEVS];   // the memory of each device
    uint32_t map_write[MAX_DEVS];           // write unit of the flash map (differs from devs[] for a SPI NOR
                                            // chip, which programs bytes)
    bool nor[MAX_DEVS];                     // the device is a SPI NOR chip behind drivers/memory/spiflash.c
    bool nor_scatter;
    host_area_t areas[5];
    unsigned n_areas;
    uint32_t pri_dev, pri_off, pri_size;
    uint32_t sec_dev, sec_off, sec_size;
    bool single;                          // slot policy single: no secondary slot
    bool overwrite;                       // slot policy overwrite-external
    bool offset;                          // swap using offset: the update starts one erase unit into its slot
    scenario_t scenario;
    const char *img_old_path;
    const char *img_new_path;
    uint8_t *img_old, *img_new;
    size_t img_old_len, img_new_len;      // file sizes
    size_t cmp_old_len, cmp_new_len;      // image bytes compared (header, body, TLVs)
    uint32_t modes;                       // bit per fake_flash_cut_mode_t
    uint32_t tears[MAX_TEARS];
    unsigned n_tears;
    unsigned stride_body;
    int max_boots;
    int only_k;
    int only_mode;
    uint32_t only_tear;
    uint32_t only_rep;
    unsigned reps;                        // random patterns of a torn unit tried per cut point
    bool verbose;
    bool strict_overprogram;
    FILE *out;
} cfg;

static att_res_t *att_res;
static bool install_next;     // the next boot attempt writes the new image over the only slot first
static trace_op_t base_trace[MAX_TRACE];
static uint32_t base_ops;
static fake_flash_snapshot_t *snap0;

// ---- helpers ----

static void die(const char *fmt, ...) __attribute__((noreturn, format(printf, 1, 2)));

static void die(const char *fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    fprintf(stderr, "host_sweep: ");
    vfprintf(stderr, fmt, ap);
    fputc('\n', stderr);
    va_end(ap);
    exit(2);
}

static uint8_t *read_file(const char *path, size_t *len) {
    FILE *f = fopen(path, "rb");
    if (f == NULL) {
        die("cannot open %s: %s", path, strerror(errno));
    }
    fseek(f, 0, SEEK_END);
    long n = ftell(f);
    fseek(f, 0, SEEK_SET);
    uint8_t *b = malloc(n);
    if (b == NULL || fread(b, 1, n, f) != (size_t)n) {
        die("cannot read %s", path);
    }
    fclose(f);
    *len = n;
    return b;
}

// Length of header + body + protected and unprotected TLVs of a signed image.
static size_t image_total_len(const uint8_t *b, size_t file_len, const char *name) {
    struct image_header hdr;
    struct image_tlv_info info;
    if (file_len < sizeof(hdr)) {
        die("%s too short", name);
    }
    memcpy(&hdr, b, sizeof(hdr));
    size_t off = (size_t)hdr.ih_hdr_size + hdr.ih_img_size + hdr.ih_protect_tlv_size;
    if (hdr.ih_magic != IMAGE_MAGIC || off + sizeof(info) > file_len) {
        die("%s is not a signed image", name);
    }
    memcpy(&info, b + off, sizeof(info));
    if (info.it_magic != IMAGE_TLV_INFO_MAGIC || off + info.it_tlv_tot > file_len) {
        die("%s has no TLV area", name);
    }
    return off + info.it_tlv_tot;
}

static int identify_primary(void) {
    const uint8_t *p = fake_flash_data(cfg.pri_dev) + cfg.pri_off;
    if (memcmp(p, cfg.img_old, cfg.cmp_old_len) == 0) {
        return 1;
    }
    if (memcmp(p, cfg.img_new, cfg.cmp_new_len) == 0) {
        return 2;
    }
    return 0;
}

static void read_slot_state(int area_id, slot_state_t *s) {
    struct boot_swap_state st;
    memset(&st, 0, sizeof(st));
    s->rc = boot_read_swap_state_by_id(area_id, &st);
    s->magic = st.magic;
    s->copy_done = st.copy_done;
    s->image_ok = st.image_ok;
    s->swap_type = st.swap_type;
}

static const char *magic_name(int m) {
    switch (m) {
        case BOOT_MAGIC_GOOD:
            return "good";
        case BOOT_MAGIC_BAD:
            return "bad";
        case BOOT_MAGIC_UNSET:
            return "unset";
        default:
            return "?";
    }
}

static const char *flag_name(int f) {
    switch (f) {
        case BOOT_FLAG_SET:
            return "set";
        case BOOT_FLAG_BAD:
            return "bad";
        case BOOT_FLAG_UNSET:
            return "unset";
        default:
            return "?";
    }
}

static const char *swap_name(int t) {
    switch (t) {
        case BOOT_SWAP_TYPE_NONE:
            return "none";
        case BOOT_SWAP_TYPE_TEST:
            return "test";
        case BOOT_SWAP_TYPE_PERM:
            return "perm";
        case BOOT_SWAP_TYPE_REVERT:
            return "revert";
        case BOOT_SWAP_TYPE_FAIL:
            return "fail";
        case BOOT_SWAP_TYPE_PANIC:
            return "panic";
        default:
            return "?";
    }
}

// ---- one boot attempt ----

static void halt_hook(const char *file, int line) {
    const char *base = strrchr(file, '/');
    snprintf(att_res->halt_file, sizeof(att_res->halt_file), "%s", base != NULL ? base + 1 : file);
    att_res->halt_line = line;
}

// The write of an update into the only slot, as the DFU session and fsload do it: each erase
// unit is erased before the first block that lands in it, blocks are written in order.
#define INSTALL_BLOCK 256u

// Erase unit of the slot at the area start: the unit of the flash map (a SPI NOR chip behind the
// map erases 4 KiB, but the map has the unit that the layout says).
static uint32_t slot_unit(const struct flash_area *fap) {
    struct flash_sector fs;
    if (flash_area_get_sector(fap, 0, &fs) != 0) {
        die("no sector at the start of a slot");
    }
    return fs.fs_size;
}

static void install_single(void) {
    const struct flash_area *fap;
    if (flash_area_open(FLASH_AREA_IMAGE_PRIMARY(0), &fap) != 0) {
        die("cannot open the primary slot");
    }
    uint32_t eu = slot_unit(fap);
    uint32_t wu = cfg.map_write[cfg.pri_dev];
    uint8_t block[INSTALL_BLOCK];
    for (size_t off = 0; off < cfg.img_new_len; off += INSTALL_BLOCK) {
        if (off % eu == 0 && flash_area_erase(fap, off, eu) != 0) {
            die("erase of the primary slot failed");
        }
        size_t n = cfg.img_new_len - off < INSTALL_BLOCK ? cfg.img_new_len - off : INSTALL_BLOCK;
        size_t padded = (n + wu - 1) / wu * wu;
        memset(block, flash_area_erased_val(fap), sizeof(block));
        memcpy(block, cfg.img_new + off, n);
        if (flash_area_write(fap, off, block, padded) != 0) {
            die("write of the primary slot failed");
        }
    }
}

static void child_boot(bool app_confirms) {
    struct boot_rsp rsp;
    FIH_DECLARE(fih_rc, FIH_FAILURE);
    uint32_t ops0 = fake_flash_op_count();
    #if defined(HOST_SHARED_GLUE)
    // The start of mboot_main(): map consistency check, then the scrub of the shadow words.
    if (mboot_flash_map_check() != 0 || mboot_flash_scrub() != 0) {
        att_res->boot_ok = 0;
        att_res->ops = fake_flash_op_count() - ops0;
        _exit(0);
    }
    #endif
    if (install_next) {
        install_single();
    }
    FIH_CALL(boot_go, fih_rc, &rsp);
    att_res->boot_ok = FIH_EQ(fih_rc, FIH_SUCCESS);
    if (att_res->boot_ok) {
        att_res->image_major = rsp.br_hdr->ih_ver.iv_major;
        if (app_confirms && rsp.br_hdr->ih_ver.iv_major == 2) {
            att_res->confirm_rc = boot_set_confirmed();
        }
    }
    att_res->ops = fake_flash_op_count() - ops0;
    // Final slot states as the next boot would see them. Read inside the child so
    // that a failing bootutil assertion cannot end the sweep process.
    read_slot_state(FLASH_AREA_IMAGE_PRIMARY(0), &att_res->pri);
    if (cfg.single) {
        att_res->swap_type = BOOT_SWAP_TYPE_NONE;     // the only slot has no swap state
    } else {
        read_slot_state(FLASH_AREA_IMAGE_SECONDARY(0), &att_res->sec);
        att_res->swap_type = boot_swap_type();
    }
    _exit(0);
}

static void run_boot(bool app_confirms, boot_info_t *bi) {
    memset(att_res, 0, sizeof(*att_res));
    memset(bi, 0, sizeof(*bi));
    fflush(NULL);
    pid_t pid = fork();
    if (pid < 0) {
        die("fork failed");
    }
    if (pid == 0) {
        host_halt_set_hook(halt_hook);
        child_boot(app_confirms);
    }
    install_next = false;
    int status = 0;
    if (waitpid(pid, &status, 0) < 0) {
        die("waitpid failed");
    }
    if (WIFSIGNALED(status)) {
        bi->end = ATT_CRASH;
    } else if (WEXITSTATUS(status) == FAKE_FLASH_CUT_EXIT) {
        bi->end = ATT_CUT;
    } else if (WEXITSTATUS(status) == 66) {
        bi->end = ATT_HALT;
    } else if (WEXITSTATUS(status) == 0) {
        bi->end = ATT_DONE;
    } else {
        bi->end = ATT_CRASH;
    }
    bi->raw_status = status;
    bi->boot_ok = att_res->boot_ok;
    bi->ops = att_res->ops;
    bi->halt_line = att_res->halt_line;
    memcpy(bi->halt_file, att_res->halt_file, sizeof(bi->halt_file));
    bi->image = identify_primary();
    bi->pri = att_res->pri;
    bi->sec = att_res->sec;
    bi->swap_type = att_res->swap_type;
}

// ---- scenario setup ----

static bool scenario_confirms(void) {
    return cfg.scenario == SCN_SWAP_CONFIRM;
}

static void build_base_state(void) {
    for (unsigned i = 0; i < cfg.n_devs; i++) {
        fake_flash_erase_raw(i, 0, cfg.devs[i].size);
    }
    fake_flash_poke(cfg.pri_dev, cfg.pri_off, cfg.img_old, cfg.img_old_len);
    if (!cfg.single) {
        const struct flash_area *sec;
        if (flash_area_open(FLASH_AREA_IMAGE_SECONDARY(0), &sec) != 0) {
            die("cannot open the secondary slot");
        }
        uint32_t spare = cfg.offset ? slot_unit(sec) : 0;
        fake_flash_poke(cfg.sec_dev, cfg.sec_off + spare, cfg.img_new, cfg.img_new_len);
    }
}

static const char *kind_name(int kind) {
    return kind == FAKE_FLASH_OP_ERASE ? "erase" : "write";
}

static bool final_allowed(int final_image) {
    switch (cfg.scenario) {
        case SCN_SWAP:
        case SCN_REVERT:
            return final_image == 1;
        default:
            return final_image == 1 || final_image == 2;
    }
}

// The overwrite-external policy erases the header of the secondary slot when the copy is done and
// its trailer state is cleared by the write that follows: a cut between the two leaves a pending
// flag on a slot that holds no image, which nothing acts on (the next update session erases the
// trailer first). That state is not a pending update.
static bool pending_without_image(void) {
    if (!cfg.overwrite) {
        return false;
    }
    uint32_t magic;
    memcpy(&magic, fake_flash_data(cfg.sec_dev) + cfg.sec_off, sizeof(magic));
    return magic != IMAGE_MAGIC;
}

// Boot repeatedly without injection until a boot performs no flash operation.
static void settle(trial_t *t) {
    for (int i = 0; i < cfg.max_boots; i++) {
        boot_info_t bi;
        run_boot(scenario_confirms(), &bi);
        t->boots++;
        t->last = bi;
        if (bi.end == ATT_HALT) {
            t->outcome = "halt";
            return;
        }
        if (bi.end != ATT_DONE) {
            t->outcome = "crash";
            return;
        }
        if (!bi.boot_ok) {
            // The single slot policy has no old image to fall back to: an update that was cut
            // leaves no valid image, the bootloader enters recovery and never starts one that
            // fails validation. That is the allowed end state (final image 0).
            t->outcome = cfg.scenario == SCN_INSTALL ? "ok" : "no_image";
            t->final_image = 0;
            return;
        }
        if (bi.image == 0) {
            t->outcome = "bad_image";
            return;
        }
        if (bi.image == 2) {
            t->saw_new = true;
        }
        if (bi.ops == 0) {
            t->final_image = bi.image;
            if (bi.swap_type != BOOT_SWAP_TYPE_NONE && !pending_without_image()) {
                t->outcome = "pending_left";
            } else if (!final_allowed(bi.image)) {
                t->outcome = "lost_revert";
            } else {
                t->outcome = "ok";
            }
            return;
        }
    }
    t->outcome = "no_converge";
}

static void run_trial(uint32_t k, fake_flash_cut_mode_t mode, uint32_t tear, unsigned rep, trial_t *t) {
    memset(t, 0, sizeof(*t));
    fake_flash_restore(snap0);
    fake_flash_arm(k, mode, tear, (k * 2654435761u ^ (tear << 8) ^ (uint32_t)mode) + rep * 0x9e3779b9u);
    boot_info_t bi;
    install_next = cfg.scenario == SCN_INSTALL;
    run_boot(scenario_confirms(), &bi);
    t->boots = 1;
    t->last = bi;
    t->cut = fake_flash_cut_fired();
    fake_flash_disarm();
    if (bi.end == ATT_HALT) {
        t->outcome = "halt";
    } else if (bi.end == ATT_CRASH) {
        t->outcome = "crash";
    } else if (!t->cut) {
        t->outcome = "no_cut";
    } else {
        settle(t);
    }
    t->violations = fake_flash_stats()->violations;
    t->overprograms = fake_flash_stats()->overprograms;
    t->ecc_events = fake_flash_stats()->ecc_events;
    if (t->violations != 0 && strcmp(t->outcome, "ok") == 0) {
        t->outcome = "violation";
    }
    if (cfg.strict_overprogram && t->overprograms != 0 && strcmp(t->outcome, "ok") == 0) {
        t->outcome = "overprogram";
    }
}

// ---- output ----

static const char *const mode_names[] = {"before", "after", "tear", "between"};

static void print_slot(FILE *f, const char *key, const slot_state_t *s) {
    fprintf(f, "\"%s\":\"magic=%s,copy_done=%s,image_ok=%s%s\"", key, magic_name(s->magic), flag_name(s->copy_done),
        flag_name(s->image_ok), s->rc != 0 ? ",unreadable" : "");
}

static void print_trial(uint32_t k, fake_flash_cut_mode_t mode, uint32_t tear, unsigned rep, const trial_t *t) {
    const trace_op_t *op = &base_trace[k - 1];
    FILE *f = cfg.out;
    fprintf(f, "{\"type\":\"trial\",\"scenario\":\"%s\",\"k\":%" PRIu32 ",\"mode\":\"%s\",\"tear\":%" PRIu32
        ",\"rep\":%u,\"op\":{\"kind\":\"%s\",\"dev\":%d,\"off\":%" PRIu32 ",\"len\":%" PRIu32 "},\"cut\":%s,\"boots\":%d,"
        "\"outcome\":\"%s\",\"final\":%d,\"saw_new\":%s,\"swap_type\":\"%s\",",
        scenario_names[cfg.scenario], k, mode_names[mode], tear, rep, kind_name(op->kind), op->dev, op->off, op->len,
        t->cut ? "true" : "false", t->boots, t->outcome, t->final_image, t->saw_new ? "true" : "false",
        swap_name(t->last.swap_type));
    print_slot(f, "pri", &t->last.pri);
    fputc(',', f);
    print_slot(f, "sec", &t->last.sec);
    fprintf(f, ",\"violations\":%" PRIu32 ",\"overprograms\":%" PRIu32 ",\"ecc_events\":%" PRIu32, t->violations,
        t->overprograms, t->ecc_events);
    if (t->last.halt_line != 0) {
        fprintf(f, ",\"halt\":\"%s:%d\"", t->last.halt_file, t->last.halt_line);
    }
    fprintf(f, "}\n");
}

static void print_trace(void) {
    FILE *f = cfg.out;
    fprintf(f, "{\"type\":\"trace\",\"scenario\":\"%s\",\"ops\":[", scenario_names[cfg.scenario]);
    for (uint32_t i = 0; i < base_ops; i++) {
        const trace_op_t *op = &base_trace[i];
        fprintf(f, "%s{\"seq\":%" PRIu32 ",\"kind\":\"%s\",\"dev\":%d,\"off\":%" PRIu32 ",\"len\":%" PRIu32 "}",
            i ? "," : "", op->seq, kind_name(op->kind), op->dev, op->off, op->len);
    }
    fprintf(f, "]}\n");
}

// ---- main ----

static unsigned long num(const char *s) {
    char *end;
    unsigned long v = strtoul(s, &end, 0);
    if (*s == '\0' || *end != '\0') {
        die("bad number '%s'", s);
    }
    return v;
}

// The erase units of a device: one number, or runs as SIZE/ERASE pairs joined by +.
static void parse_erase(fake_flash_dev_cfg_t *d, char *field) {
    if (strchr(field, '/') == NULL) {
        d->erase_unit = num(field);
        return;
    }
    for (char *run = strtok(field, "+"); run != NULL; run = strtok(NULL, "+")) {
        char *slash = strchr(run, '/');
        if (slash == NULL || d->n_runs >= FAKE_FLASH_MAX_RUNS) {
            die("--dev runs are SIZE/ERASE pairs, at most %d", FAKE_FLASH_MAX_RUNS);
        }
        *slash = '\0';
        d->runs[d->n_runs].size = num(run);
        d->runs[d->n_runs].erase = num(slash + 1);
        d->n_runs++;
    }
}

static void parse_dev(const char *spec) {
    if (cfg.n_devs >= MAX_DEVS) {
        die("too many --dev");
    }
    char buf[256];
    snprintf(buf, sizeof(buf), "%s", spec);
    char *f[7] = {0};
    int n = 0;
    for (char *p = strtok(buf, ":"); p != NULL && n < 7; p = strtok(NULL, ":")) {
        f[n++] = p;
    }
    if (n < 4) {
        die("--dev SIZE:ERASE:WRITE:ECC[:ERASED[:MAPBASE[:NOR]]], ERASE is a unit or SIZE/ERASE runs joined by +");
    }
    fake_flash_dev_cfg_t *d = &cfg.devs[cfg.n_devs];
    d->size = num(f[0]);
    parse_erase(d, f[1]);
    d->write_unit = num(f[2]);
    d->ecc = num(f[3]) != 0;
    d->erased_val = n > 4 ? num(f[4]) : 0xff;
    d->map_base = n > 5 ? num(f[5]) : 0;
    cfg.map_write[cfg.n_devs] = d->write_unit;
    cfg.nor[cfg.n_devs] = n > 6 && num(f[6]) != 0;
    cfg.n_devs++;
}

static void parse_area(const char *spec) {
    const char *eq = strchr(spec, '=');
    if (eq == NULL) {
        die("--area NAME=DEV:OFF:SIZE");
    }
    char buf[96];
    snprintf(buf, sizeof(buf), "%s", eq + 1);
    char *f[3] = {0};
    int n = 0;
    for (char *p = strtok(buf, ":"); p != NULL && n < 3; p = strtok(NULL, ":")) {
        f[n++] = p;
    }
    if (n != 3) {
        die("--area NAME=DEV:OFF:SIZE");
    }
    host_area_t a;
    a.dev = num(f[0]);
    a.off = num(f[1]);
    a.size = num(f[2]);
    if (strncmp(spec, "primary=", 8) == 0) {
        a.id = FLASH_AREA_IMAGE_PRIMARY(0);
        cfg.pri_dev = a.dev;
        cfg.pri_off = a.off;
        cfg.pri_size = a.size;
    } else if (strncmp(spec, "secondary=", 10) == 0) {
        a.id = FLASH_AREA_IMAGE_SECONDARY(0);
        cfg.sec_dev = a.dev;
        cfg.sec_off = a.off;
        cfg.sec_size = a.size;
    } else if (strncmp(spec, "scratch=", 8) == 0) {
        a.id = FLASH_AREA_IMAGE_SCRATCH;
    } else {
        die("unknown area in '%s'", spec);
    }
    cfg.areas[cfg.n_areas++] = a;
}

static int mode_from_name(const char *s) {
    for (int i = 0; i < 4; i++) {
        if (strcmp(s, mode_names[i]) == 0) {
            return i;
        }
    }
    die("unknown mode '%s'", s);
}

static void usage(void) {
    fprintf(stderr,
        "usage: host_sweep --dev SIZE:ERASE:WRITE:ECC[:ERASED[:MAPBASE[:NOR]]] ... --area primary=DEV:OFF:SIZE [--area secondary=DEV:OFF:SIZE]\n"
        "                  [--area scratch=DEV:OFF:SIZE] [--policy swap|overwrite-external|single] [--mode offset|move|scratch|none]\n"
        "                  --scenario swap|swap_confirm|revert|perm|install --old-image FILE --new-image FILE\n"
        "                  [--modes before,after,between,tear] [--tears 64,128,192] [--stride-body N] [--max-boots N]\n"
        "                  [--out FILE] [--strict-overprogram] [--nor-scatter] [--reps N]\n"
        "                  [--only K:MODE:TEAR[:REP]] [-v]\n");
    exit(2);
}

int main(int argc, char **argv) {
    cfg.modes = (1u << FAKE_FLASH_CUT_BEFORE) | (1u << FAKE_FLASH_CUT_AFTER) | (1u << FAKE_FLASH_CUT_BETWEEN);
    cfg.tears[0] = 64;
    cfg.tears[1] = 128;
    cfg.tears[2] = 192;
    cfg.n_tears = 3;
    cfg.stride_body = 1;
    cfg.max_boots = 8;
    cfg.out = stdout;
    cfg.only_k = -1;
    cfg.reps = 1;
    bool have_scenario = false;

    for (int i = 1; i < argc; i++) {
        const char *a = argv[i];
        const char *v = i + 1 < argc ? argv[i + 1] : NULL;
        if (strcmp(a, "--dev") == 0 && v) {
            parse_dev(v);
            i++;
        } else if (strcmp(a, "--area") == 0 && v) {
            parse_area(v);
            i++;
        } else if (strcmp(a, "--policy") == 0 && v) {
            cfg.single = strcmp(v, "single") == 0;
            cfg.overwrite = strcmp(v, "overwrite-external") == 0;
            if (!cfg.single && strcmp(v, "swap") != 0 && strcmp(v, "overwrite-external") != 0) {
                die("unknown policy '%s'", v);
            }
            i++;
        } else if (strcmp(a, "--mode") == 0 && v) {
            cfg.offset = strcmp(v, "offset") == 0;
            i++;
        } else if (strcmp(a, "--scenario") == 0 && v) {
            int s;
            for (s = 0; s < N_SCENARIOS && strcmp(v, scenario_names[s]) != 0; s++) {
            }
            if (s == N_SCENARIOS) {
                die("unknown scenario '%s'", v);
            }
            cfg.scenario = s;
            have_scenario = true;
            i++;
        } else if (strcmp(a, "--old-image") == 0 && v) {
            cfg.img_old_path = v;
            i++;
        } else if (strcmp(a, "--new-image") == 0 && v) {
            cfg.img_new_path = v;
            i++;
        } else if (strcmp(a, "--modes") == 0 && v) {
            cfg.modes = 0;
            char buf[128];
            snprintf(buf, sizeof(buf), "%s", v);
            for (char *p = strtok(buf, ","); p != NULL; p = strtok(NULL, ",")) {
                cfg.modes |= 1u << mode_from_name(p);
            }
            i++;
        } else if (strcmp(a, "--tears") == 0 && v) {
            cfg.n_tears = 0;
            char buf[128];
            snprintf(buf, sizeof(buf), "%s", v);
            for (char *p = strtok(buf, ","); p != NULL; p = strtok(NULL, ",")) {
                if (cfg.n_tears >= MAX_TEARS) {
                    die("at most %d --tears", MAX_TEARS);
                }
                cfg.tears[cfg.n_tears++] = num(p);
            }
            i++;
        } else if (strcmp(a, "--stride-body") == 0 && v) {
            cfg.stride_body = num(v);
            i++;
        } else if (strcmp(a, "--max-boots") == 0 && v) {
            cfg.max_boots = num(v);
            i++;
        } else if (strcmp(a, "--out") == 0 && v) {
            cfg.out = fopen(v, "w");
            if (cfg.out == NULL) {
                die("cannot open %s", v);
            }
            i++;
        } else if (strcmp(a, "--only") == 0 && v) {
            char buf[64];
            snprintf(buf, sizeof(buf), "%s", v);
            char *k = strtok(buf, ":");
            char *m = strtok(NULL, ":");
            char *t = strtok(NULL, ":");
            char *r = strtok(NULL, ":");
            if (k == NULL || m == NULL) {
                usage();
            }
            cfg.only_k = num(k);
            cfg.only_mode = mode_from_name(m);
            cfg.only_tear = t != NULL ? num(t) : 0;
            cfg.only_rep = r != NULL ? num(r) : 0;
            i++;
        } else if (strcmp(a, "--strict-overprogram") == 0) {
            cfg.strict_overprogram = true;
        } else if (strcmp(a, "--reps") == 0 && v) {
            cfg.reps = num(v);
            i++;
        } else if (strcmp(a, "--nor-scatter") == 0) {
            cfg.nor_scatter = true;
        } else if (strcmp(a, "-v") == 0) {
            cfg.verbose = true;
        } else {
            usage();
        }
    }
    if (!have_scenario || cfg.n_devs == 0 || cfg.n_areas < (cfg.single ? 1 : 2) || (cfg.scenario == SCN_INSTALL) != cfg.single || cfg.img_old_path == NULL || cfg.img_new_path == NULL) {
        usage();
    }

    cfg.img_old = read_file(cfg.img_old_path, &cfg.img_old_len);
    cfg.img_new = read_file(cfg.img_new_path, &cfg.img_new_len);
    cfg.cmp_old_len = image_total_len(cfg.img_old, cfg.img_old_len, cfg.img_old_path);
    cfg.cmp_new_len = image_total_len(cfg.img_new, cfg.img_new_len, cfg.img_new_path);

    for (unsigned i = 0; i < cfg.n_devs; i++) {
        if (cfg.nor[i]) {
            #if defined(HOST_SHARED_GLUE)
            spi_nor_dev_cfg(&cfg.devs[i], cfg.devs[i].size, cfg.nor_scatter);
            #else
            die("a SPI NOR device needs GLUE=shared");
            #endif
        }
    }
    int rc = fake_flash_init(cfg.n_devs, cfg.devs);
    if (rc != 0) {
        die("fake_flash_init failed: %d", rc);
    }
    #if defined(HOST_SHARED_GLUE)
    for (unsigned i = 0; i < cfg.n_devs; i++) {
        if (cfg.nor[i] && host_port_flash_attach_spi_nor(i) != 0) {
            die("cannot attach the SPI NOR model to device %u", i);
        }
    }
    #endif
    att_res = mmap(NULL, sizeof(*att_res), PROT_READ | PROT_WRITE, MAP_SHARED | MAP_ANONYMOUS, -1, 0);
    if (att_res == MAP_FAILED) {
        die("mmap failed");
    }
    #if !defined(HOST_SHARED_GLUE)
    host_map_init(cfg.areas, cfg.n_areas);
    #endif
    host_log_set_file(cfg.verbose ? stderr : NULL);

    build_base_state();
    if (cfg.scenario == SCN_REVERT) {
        // Run the swap boot once without injection: the new image is then
        // running unconfirmed and the next boot reverts it.
        boot_info_t bi;
        run_boot(false, &bi);
        if (bi.end != ATT_DONE || !bi.boot_ok || bi.image != 2 || bi.ops == 0) {
            die("revert setup: swap boot did not install the new image (end=%d status=0x%x ok=%d image=%d ops=%u)", bi.end,
                bi.raw_status, bi.boot_ok, bi.image, bi.ops);
        }
    }
    snap0 = fake_flash_snapshot();
    if (snap0 == NULL) {
        die("snapshot failed");
    }

    // Baseline: the cuttable window is the first boot attempt.
    fake_flash_restore(snap0);
    boot_info_t bi;
    install_next = cfg.scenario == SCN_INSTALL;
    run_boot(scenario_confirms(), &bi);
    if (bi.end != ATT_DONE || !bi.boot_ok || bi.ops == 0) {
        die("baseline boot failed (end=%d status=0x%x ok=%d ops=%u)", bi.end, bi.raw_status, bi.boot_ok, bi.ops);
    }
    base_ops = fake_flash_op_count();
    if (base_ops > MAX_TRACE || fake_flash_trace_count() != base_ops) {
        die("baseline trace unusable (ops=%u traced=%zu)", base_ops, fake_flash_trace_count());
    }
    for (uint32_t i = 0; i < base_ops; i++) {
        const fake_flash_op_t *op = &fake_flash_trace()[i];
        base_trace[i] = (trace_op_t) {op->seq, op->kind, op->dev, op->off, op->len};
    }
    {
        trial_t base;
        memset(&base, 0, sizeof(base));
        base.boots = 1;
        base.last = bi;
        base.saw_new = bi.image == 2;
        settle(&base);
        fprintf(stderr, "baseline %s: %u operations in the cuttable boot, settles after %d more boots on image %d (%s)\n",
            scenario_names[cfg.scenario], base_ops, base.boots - 1, base.final_image, base.outcome);
        if (strcmp(base.outcome, "ok") != 0) {
            die("baseline does not settle to an allowed state");
        }
    }
    print_trace();

    unsigned total = 0, failed = 0;
    for (int mode = 0; mode < 4; mode++) {
        if (!(cfg.modes & (1u << mode))) {
            continue;
        }
        bool partial = mode == FAKE_FLASH_CUT_DURING || mode == FAKE_FLASH_CUT_BETWEEN;
        unsigned ntear = partial ? cfg.n_tears : 1;
        unsigned body_seen = 0;
        for (uint32_t k = 1; k <= base_ops; k++) {
            const trace_op_t *op = &base_trace[k - 1];
            uint32_t wu = cfg.devs[op->dev].write_unit;
            bool multi = op->kind == FAKE_FLASH_OP_ERASE || op->len > wu;
            if (mode == FAKE_FLASH_CUT_BETWEEN && !multi) {
                continue;
            }
            if (op->kind == FAKE_FLASH_OP_WRITE && op->len > cfg.map_write[op->dev] && k != 1 && k != base_ops) {
                if (body_seen++ % cfg.stride_body != 0) {
                    continue;
                }
            }
            for (unsigned ti = 0; ti < ntear; ti++) {
                uint32_t tear = partial ? cfg.tears[ti] : 0;
                // The random bits of a torn unit differ per rep; the other modes have none.
                unsigned nrep = mode == FAKE_FLASH_CUT_DURING ? cfg.reps : 1;
                for (unsigned rep = 0; rep < nrep; rep++) {
                    if (cfg.only_k >= 0 && (k != (uint32_t)cfg.only_k || mode != cfg.only_mode || (partial && tear != cfg.only_tear) || rep != cfg.only_rep)) {
                        continue;
                    }
                    trial_t t;
                    run_trial(k, mode, tear, rep, &t);
                    print_trial(k, mode, tear, rep, &t);
                    total++;
                    if (strcmp(t.outcome, "ok") != 0) {
                        failed++;
                    }
                }
            }
        }
    }
    fprintf(cfg.out, "{\"type\":\"summary\",\"scenario\":\"%s\",\"trials\":%u,\"failed\":%u}\n", scenario_names[cfg.scenario],
        total, failed);
    fclose(cfg.out == stdout ? stdout : cfg.out);
    return failed != 0;
}
