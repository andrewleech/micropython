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
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>

#include "fake_flash.h"

#define TRACE_MAX (1u << 16)

#define DIRTY_BLOCK (4096u)

#define UNIT_BAD (1)
#define UNIT_WEAK (2)

typedef struct {
    fake_flash_dev_cfg_t cfg;
    uint8_t *mem;
    uint8_t *prog;      // one byte per write unit: holds non-erased data
    uint8_t *bad;       // one byte per write unit: UNIT_BAD (ECC-invalid) or UNIT_WEAK (corrected on read)
    uint8_t *dirty;     // one byte per DIRTY_BLOCK bytes: changed since the snapshot that restore() tracks
    uint32_t n_units;
} ff_dev_t;

typedef struct {
    uint32_t counter;
    uint32_t target;
    uint32_t mode;
    uint32_t tear_q8;
    uint32_t seed;
    uint32_t ecc_events;
    uint32_t corrected_events;
    uint32_t cut_fired;
    size_t trace_count;
    fake_flash_stats_t stats;
    fake_flash_op_t trace[TRACE_MAX];
} ctl_t;

struct fake_flash_snapshot {
    unsigned n_devs;
    uint8_t *mem[FAKE_FLASH_MAX_DEVS];
    uint8_t *prog[FAKE_FLASH_MAX_DEVS];
    uint8_t *bad[FAKE_FLASH_MAX_DEVS];
};

static ff_dev_t devs[FAKE_FLASH_MAX_DEVS];
static const fake_flash_snapshot_t *tracked_snap;
static unsigned n_devs;
static ctl_t *ctl;
static fake_flash_cut_handler_t cut_handler;
static void *cut_ctx;

static void *shared_alloc(size_t n) {
    void *p = mmap(NULL, n, PROT_READ | PROT_WRITE, MAP_SHARED | MAP_ANONYMOUS, -1, 0);
    return p == MAP_FAILED ? NULL : p;
}

static void *shared_alloc_at(uintptr_t addr, size_t n) {
    void *p = mmap((void *)addr, n, PROT_READ | PROT_WRITE, MAP_SHARED | MAP_ANONYMOUS | MAP_FIXED_NOREPLACE, -1, 0);
    if (p == MAP_FAILED) {
        return NULL;
    }
    if ((uintptr_t)p != addr) {
        munmap(p, n);
        return NULL;
    }
    return p;
}

static bool is_pow2(uint32_t v) {
    return v != 0 && (v & (v - 1)) == 0;
}

void fake_flash_deinit(void) {
    for (unsigned i = 0; i < n_devs; i++) {
        munmap(devs[i].mem, devs[i].cfg.size);
        munmap(devs[i].prog, devs[i].n_units);
        munmap(devs[i].bad, devs[i].n_units);
        munmap(devs[i].dirty, (devs[i].cfg.size + DIRTY_BLOCK - 1) / DIRTY_BLOCK);
    }
    n_devs = 0;
    tracked_snap = NULL;
    if (ctl != NULL) {
        munmap(ctl, sizeof(*ctl));
        ctl = NULL;
    }
}

// The erase units of a device: one all over, or runs that add up to its size.
static bool cfg_ok(const fake_flash_dev_cfg_t *c) {
    if (!is_pow2(c->write_unit) || c->write_unit > 32 || c->size == 0
        || (c->erased_val != 0xff && c->erased_val != 0x00) || c->n_runs > FAKE_FLASH_MAX_RUNS) {
        return false;
    }
    if (c->n_runs == 0) {
        return is_pow2(c->erase_unit) && c->erase_unit % c->write_unit == 0 && c->size % c->erase_unit == 0;
    }
    uint32_t total = 0;
    for (unsigned i = 0; i < c->n_runs; i++) {
        uint32_t e = c->runs[i].erase;
        if (!is_pow2(e) || e % c->write_unit != 0 || c->runs[i].size == 0 || c->runs[i].size % e != 0 || total % e != 0) {
            return false;
        }
        total += c->runs[i].size;
    }
    return total == c->size;
}

int fake_flash_init(unsigned n, const fake_flash_dev_cfg_t *cfg) {
    fake_flash_deinit();
    if (n == 0 || n > FAKE_FLASH_MAX_DEVS) {
        return -EINVAL;
    }
    ctl = shared_alloc(sizeof(*ctl));
    if (ctl == NULL) {
        return -ENOMEM;
    }
    memset(ctl, 0, sizeof(*ctl));
    for (unsigned i = 0; i < n; i++) {
        const fake_flash_dev_cfg_t *c = &cfg[i];
        if (!cfg_ok(c)) {
            fake_flash_deinit();
            return -EINVAL;
        }
        ff_dev_t *d = &devs[i];
        d->cfg = *c;
        d->n_units = c->size / c->write_unit;
        d->mem = c->map_base != 0 ? shared_alloc_at(c->map_base, c->size) : shared_alloc(c->size);
        d->prog = shared_alloc(d->n_units);
        d->bad = shared_alloc(d->n_units);
        d->dirty = shared_alloc((c->size + DIRTY_BLOCK - 1) / DIRTY_BLOCK);
        if (d->mem == NULL || d->prog == NULL || d->bad == NULL || d->dirty == NULL) {
            n_devs = i + 1;
            fake_flash_deinit();
            return -ENOMEM;
        }
        memset(d->mem, c->erased_val, c->size);
        // prog, bad and dirty are zero from the mapping; touching them would commit all the pages.
    }
    n_devs = n;
    return 0;
}

unsigned fake_flash_dev_count(void) {
    return n_devs;
}

const fake_flash_dev_cfg_t *fake_flash_dev_cfg(uint8_t dev) {
    return dev < n_devs ? &devs[dev].cfg : NULL;
}

static uint32_t cfg_erase_at(const fake_flash_dev_cfg_t *c, uint32_t off) {
    if (off >= c->size) {
        return 0;
    }
    if (c->n_runs == 0) {
        return c->erase_unit;
    }
    uint32_t end = 0;
    for (unsigned i = 0; i < c->n_runs; i++) {
        end += c->runs[i].size;
        if (off < end) {
            return c->runs[i].erase;
        }
    }
    return 0;
}

uint32_t fake_flash_erase_at(uint8_t dev, uint32_t off) {
    return dev < n_devs ? cfg_erase_at(&devs[dev].cfg, off) : 0;
}

// A range of whole erase units, which may cross from one run into the next.
static bool erase_range_ok(const fake_flash_dev_cfg_t *c, uint32_t off, uint32_t len) {
    if (len == 0 || off > c->size || len > c->size - off) {
        return false;
    }
    for (uint32_t end = off + len; off < end;) {
        uint32_t unit = cfg_erase_at(c, off);
        if (unit == 0 || off % unit != 0 || end - off < unit) {
            return false;
        }
        off += unit;
    }
    return true;
}

// ---- unit state helpers ----

static bool buf_all(const uint8_t *p, uint32_t n, uint8_t v) {
    for (uint32_t i = 0; i < n; i++) {
        if (p[i] != v) {
            return false;
        }
    }
    return true;
}

static uint32_t next_rand(void) {
    uint32_t x = ctl->seed ? ctl->seed : 0x9E3779B9u;
    x ^= x << 13;
    x ^= x >> 17;
    x ^= x << 5;
    ctl->seed = x;
    return x;
}

static void mark_dirty(ff_dev_t *d, uint32_t off, uint32_t len) {
    if (len != 0) {
        memset(d->dirty + off / DIRTY_BLOCK, 1, (off + len - 1) / DIRTY_BLOCK - off / DIRTY_BLOCK + 1);
    }
}

static void erase_unit(ff_dev_t *d, uint32_t u) {
    memset(d->mem + (size_t)u * d->cfg.write_unit, d->cfg.erased_val, d->cfg.write_unit);
    d->prog[u] = 0;
    d->bad[u] = 0;
}

// Complete program of one unit.
static void program_unit(ff_dev_t *d, uint32_t u, const uint8_t *data) {
    uint32_t wu = d->cfg.write_unit;
    uint8_t *p = d->mem + (size_t)u * wu;
    uint8_t ev = d->cfg.erased_val;
    if (!d->prog[u] && !d->bad[u]) {
        memcpy(p, data, wu);
        d->prog[u] = !buf_all(data, wu, ev);
        return;
    }
    if (!d->bad[u] && memcmp(p, data, wu) == 0) {
        return;
    }
    ctl->stats.overprograms++;
    for (uint32_t i = 0; i < wu; i++) {
        p[i] = ev == 0xff ? (p[i] & data[i]) : (p[i] | data[i]);
    }
    d->prog[u] = 1;
    if (d->cfg.ecc) {
        d->bad[u] = UNIT_BAD;
    }
}

// Unit whose program was interrupted: a random subset of the bits that should
// have changed has changed. A weak unit reads corrected: with an odd seed it reads erased.
static void tear_program_unit(ff_dev_t *d, uint32_t u, const uint8_t *data, bool weak) {
    uint32_t wu = d->cfg.write_unit;
    uint8_t *p = d->mem + (size_t)u * wu;
    uint8_t ev = d->cfg.erased_val;
    if (d->cfg.atomic_program) {
        if (next_rand() & 1) {
            for (uint32_t i = 0; i < wu; i++) {
                p[i] = ev == 0xff ? (p[i] & data[i]) : (p[i] | data[i]);
            }
            d->prog[u] = 1;
        }
        return;
    }
    if (weak && d->cfg.ecc && (ctl->seed & 1)) {
        d->prog[u] = 1;
        d->bad[u] = UNIT_WEAK;
        return;
    }
    for (uint32_t i = 0; i < wu; i++) {
        uint8_t m = (uint8_t)next_rand();
        if (ev == 0xff) {
            p[i] = p[i] & (data[i] | m);
        } else {
            p[i] = p[i] | (data[i] & m);
        }
    }
    d->prog[u] = 1;
    if (d->cfg.ecc) {
        d->bad[u] = weak ? UNIT_WEAK : UNIT_BAD;
    }
}

// Unit whose erase was interrupted: a random subset of the programmed bits has
// returned to the erased value.
static void tear_erase_unit(ff_dev_t *d, uint32_t u, bool weak) {
    uint32_t wu = d->cfg.write_unit;
    uint8_t *p = d->mem + (size_t)u * wu;
    uint8_t ev = d->cfg.erased_val;
    for (uint32_t i = 0; i < wu; i++) {
        uint8_t m = (uint8_t)next_rand();
        p[i] = ev == 0xff ? (p[i] | m) : (p[i] & (uint8_t) ~m);
    }
    d->prog[u] = 1;
    if (d->cfg.ecc) {
        d->bad[u] = weak ? UNIT_WEAK : UNIT_BAD;
    }
}

// ---- backdoor ----

void fake_flash_poke(uint8_t dev, uint32_t off, const void *src, uint32_t len) {
    ff_dev_t *d = &devs[dev];
    uint32_t wu = d->cfg.write_unit;
    mark_dirty(d, off, len);
    memcpy(d->mem + off, src, len);
    for (uint32_t u = off / wu; u * wu < off + len; u++) {
        d->prog[u] = !buf_all(d->mem + (size_t)u * wu, wu, d->cfg.erased_val);
        d->bad[u] = 0;
    }
}

void fake_flash_peek(uint8_t dev, uint32_t off, void *dst, uint32_t len) {
    memcpy(dst, devs[dev].mem + off, len);
}

void fake_flash_erase_raw(uint8_t dev, uint32_t off, uint32_t len) {
    ff_dev_t *d = &devs[dev];
    uint32_t wu = d->cfg.write_unit;
    mark_dirty(d, off, len);
    for (uint32_t u = off / wu; u * wu < off + len; u++) {
        erase_unit(d, u);
    }
}

void fake_flash_set_weak(uint8_t dev, uint32_t off, const void *data) {
    ff_dev_t *d = &devs[dev];
    uint32_t u = off / d->cfg.write_unit;
    mark_dirty(d, u * d->cfg.write_unit, d->cfg.write_unit);
    if (data != NULL) {
        memcpy(d->mem + (size_t)u * d->cfg.write_unit, data, d->cfg.write_unit);
    }
    d->prog[u] = 1;
    d->bad[u] = UNIT_WEAK;
}

const uint8_t *fake_flash_data(uint8_t dev) {
    return devs[dev].mem;
}

bool fake_flash_unit_ecc_invalid(uint8_t dev, uint32_t off) {
    return devs[dev].bad[off / devs[dev].cfg.write_unit] == UNIT_BAD;
}

unsigned fake_flash_count_ecc_invalid(uint8_t dev, uint32_t off, uint32_t len) {
    ff_dev_t *d = &devs[dev];
    uint32_t wu = d->cfg.write_unit;
    unsigned n = 0;
    if (len == 0) {
        return 0;
    }
    for (uint32_t u = off / wu; u <= (off + len - 1) / wu; u++) {
        n += d->bad[u] == UNIT_BAD;
    }
    return n;
}

unsigned fake_flash_count_corrected(uint8_t dev, uint32_t off, uint32_t len) {
    ff_dev_t *d = &devs[dev];
    uint32_t wu = d->cfg.write_unit;
    unsigned n = 0;
    if (len == 0) {
        return 0;
    }
    for (uint32_t u = off / wu; u <= (off + len - 1) / wu; u++) {
        n += d->bad[u] == UNIT_WEAK;
    }
    return n;
}

// ---- snapshots ----

fake_flash_snapshot_t *fake_flash_snapshot(void) {
    fake_flash_snapshot_t *s = calloc(1, sizeof(*s));
    if (s == NULL) {
        return NULL;
    }
    s->n_devs = n_devs;
    for (unsigned i = 0; i < n_devs; i++) {
        s->mem[i] = malloc(devs[i].cfg.size);
        s->prog[i] = malloc(devs[i].n_units);
        s->bad[i] = malloc(devs[i].n_units);
        if (s->mem[i] == NULL || s->prog[i] == NULL || s->bad[i] == NULL) {
            fake_flash_snapshot_free(s);
            return NULL;
        }
        memcpy(s->mem[i], devs[i].mem, devs[i].cfg.size);
        memcpy(s->prog[i], devs[i].prog, devs[i].n_units);
        memcpy(s->bad[i], devs[i].bad, devs[i].n_units);
        memset(devs[i].dirty, 0, (devs[i].cfg.size + DIRTY_BLOCK - 1) / DIRTY_BLOCK);
    }
    tracked_snap = s;
    return s;
}

void fake_flash_restore(const fake_flash_snapshot_t *s) {
    // Only the blocks written since the snapshot that was last taken or restored differ from it.
    bool tracked = s == tracked_snap;
    for (unsigned i = 0; i < s->n_devs && i < n_devs; i++) {
        ff_dev_t *d = &devs[i];
        uint32_t wu = d->cfg.write_unit;
        uint32_t blocks = (d->cfg.size + DIRTY_BLOCK - 1) / DIRTY_BLOCK;
        for (uint32_t b = 0; b < blocks; b++) {
            if (tracked && !d->dirty[b]) {
                continue;
            }
            uint32_t len = d->cfg.size - b * DIRTY_BLOCK < DIRTY_BLOCK ? d->cfg.size - b * DIRTY_BLOCK : DIRTY_BLOCK;
            memcpy(d->mem + (size_t)b * DIRTY_BLOCK, s->mem[i] + (size_t)b * DIRTY_BLOCK, len);
            memcpy(d->prog + (size_t)b * DIRTY_BLOCK / wu, s->prog[i] + (size_t)b * DIRTY_BLOCK / wu, len / wu);
            memcpy(d->bad + (size_t)b * DIRTY_BLOCK / wu, s->bad[i] + (size_t)b * DIRTY_BLOCK / wu, len / wu);
            d->dirty[b] = 0;
        }
    }
    tracked_snap = s;
    memset(ctl, 0, offsetof(ctl_t, trace));
}

void fake_flash_snapshot_free(fake_flash_snapshot_t *s) {
    if (s == NULL) {
        return;
    }
    if (s == tracked_snap) {
        tracked_snap = NULL;
    }
    for (unsigned i = 0; i < FAKE_FLASH_MAX_DEVS; i++) {
        free(s->mem[i]);
        free(s->prog[i]);
        free(s->bad[i]);
    }
    free(s);
}

// ---- injection and accounting ----

void fake_flash_arm(uint32_t target, fake_flash_cut_mode_t mode, uint32_t tear_q8, uint32_t seed) {
    ctl->target = target;
    ctl->mode = mode;
    ctl->tear_q8 = tear_q8 > 255 ? 255 : tear_q8;
    ctl->seed = seed;
    ctl->cut_fired = 0;
}

void fake_flash_disarm(void) {
    ctl->target = 0;
}

bool fake_flash_cut_fired(void) {
    return ctl->cut_fired != 0;
}

void fake_flash_set_cut_handler(fake_flash_cut_handler_t fn, void *ctx) {
    cut_handler = fn;
    cut_ctx = ctx;
}

uint32_t fake_flash_op_count(void) {
    return ctl->counter;
}

void fake_flash_counter_reset(void) {
    ctl->counter = 0;
    ctl->trace_count = 0;
    memset(&ctl->stats, 0, sizeof(ctl->stats));
}

const fake_flash_stats_t *fake_flash_stats(void) {
    return &ctl->stats;
}

size_t fake_flash_trace_count(void) {
    return ctl->trace_count;
}

const fake_flash_op_t *fake_flash_trace(void) {
    return ctl->trace;
}

// Allocates the trace entry for the next operation; returns NULL if the trace is full.
static fake_flash_op_t *op_begin(fake_flash_op_kind_t kind, uint8_t dev, uint32_t off, uint32_t len) {
    ctl->counter++;
    if (ctl->trace_count >= TRACE_MAX) {
        ctl->stats.trace_dropped++;
        return NULL;
    }
    fake_flash_op_t *op = &ctl->trace[ctl->trace_count++];
    op->seq = ctl->counter;
    op->kind = kind;
    op->dev = dev;
    op->flags = 0;
    op->off = off;
    op->len = len;
    return op;
}

static void cut_now(fake_flash_op_t *op) {
    if (op != NULL) {
        op->flags |= FAKE_FLASH_OPF_CUT;
    }
    ctl->cut_fired = 1;
    ctl->target = 0;
    if (cut_handler != NULL) {
        cut_handler(cut_ctx);
    }
    _exit(FAKE_FLASH_CUT_EXIT);
}

static bool op_hit(void) {
    return ctl->target != 0 && ctl->counter == ctl->target;
}

// ---- port functions ----

void fake_flash_note_violation(void) {
    ctl->stats.violations++;
}

int fake_flash_dev_read(uint8_t dev, uint32_t off, void *dst, uint32_t len) {
    if (dev >= n_devs) {
        return -ENODEV;
    }
    ff_dev_t *d = &devs[dev];
    if (off > d->cfg.size || len > d->cfg.size - off) {
        ctl->stats.violations++;
        return -EINVAL;
    }
    ctl->stats.reads++;
    ctl->stats.dev_reads[dev]++;
    memcpy(dst, d->mem + off, len);
    if (d->cfg.ecc && len != 0) {
        unsigned c = fake_flash_count_corrected(dev, off, len);
        ctl->corrected_events += c;
        ctl->stats.corrected_events += c;
        unsigned n = fake_flash_count_ecc_invalid(dev, off, len);
        if (n != 0) {
            ctl->ecc_events += n;
            ctl->stats.ecc_events += n;
            return -EIO;
        }
    }
    return 0;
}

int fake_flash_dev_write(uint8_t dev, uint32_t off, const void *src, uint32_t len) {
    if (dev >= n_devs) {
        return -ENODEV;
    }
    ff_dev_t *d = &devs[dev];
    uint32_t wu = d->cfg.write_unit;
    fake_flash_op_t *op = op_begin(FAKE_FLASH_OP_WRITE, dev, off, len);
    ctl->stats.writes++;
    if (len == 0 || off % wu != 0 || len % wu != 0 || off > d->cfg.size || len > d->cfg.size - off) {
        ctl->stats.violations++;
        if (op != NULL) {
            op->flags |= FAKE_FLASH_OPF_ERROR;
        }
        return -EINVAL;
    }
    const uint8_t *data = src;
    uint32_t n = len / wu;
    uint32_t u0 = off / wu;
    mark_dirty(d, off, len);
    bool hit = op_hit();
    if (hit && ctl->mode == FAKE_FLASH_CUT_BEFORE) {
        cut_now(op);
    }
    uint32_t done = n;
    bool partial = hit && (ctl->mode == FAKE_FLASH_CUT_DURING || ctl->mode == FAKE_FLASH_CUT_BETWEEN || ctl->mode == FAKE_FLASH_CUT_WEAK);
    if (partial) {
        done = (uint32_t)(((uint64_t)n * ctl->tear_q8) >> 8);
    }
    bool verify_failed = false;
    for (uint32_t i = 0; i < done; i++) {
        // The port driver reads every programmed unit back: a unit that was corrected on read
        // before the program cannot take the data (it ends up ECC-invalid) and the call fails.
        verify_failed |= d->bad[u0 + i] == UNIT_WEAK;
        program_unit(d, u0 + i, data + (size_t)i * wu);
    }
    if (partial) {
        if (ctl->mode == FAKE_FLASH_CUT_DURING || ctl->mode == FAKE_FLASH_CUT_WEAK) {
            // A device that scatters a cut leaves every later unit of the command torn as well.
            uint32_t last = d->cfg.tear_scatter ? n : done + 1;
            for (uint32_t i = done; i < last; i++) {
                tear_program_unit(d, u0 + i, data + (size_t)i * wu, ctl->mode == FAKE_FLASH_CUT_WEAK);
            }
        }
        cut_now(op);
    }
    if (hit && ctl->mode == FAKE_FLASH_CUT_AFTER) {
        cut_now(op);
    }
    return verify_failed ? -EIO : 0;
}

int fake_flash_dev_erase(uint8_t dev, uint32_t off, uint32_t len) {
    if (dev >= n_devs) {
        return -ENODEV;
    }
    ff_dev_t *d = &devs[dev];
    uint32_t wu = d->cfg.write_unit;
    fake_flash_op_t *op = op_begin(FAKE_FLASH_OP_ERASE, dev, off, len);
    ctl->stats.erases++;
    if (!erase_range_ok(&d->cfg, off, len)) {
        ctl->stats.violations++;
        if (op != NULL) {
            op->flags |= FAKE_FLASH_OPF_ERROR;
        }
        return -EINVAL;
    }
    uint32_t n = len / wu;
    uint32_t u0 = off / wu;
    mark_dirty(d, off, len);
    bool hit = op_hit();
    if (hit && ctl->mode == FAKE_FLASH_CUT_BEFORE) {
        cut_now(op);
    }
    uint32_t done = n;
    bool partial = hit && (ctl->mode == FAKE_FLASH_CUT_DURING || ctl->mode == FAKE_FLASH_CUT_BETWEEN || ctl->mode == FAKE_FLASH_CUT_WEAK);
    if (partial) {
        done = (uint32_t)(((uint64_t)n * ctl->tear_q8) >> 8);
    }
    for (uint32_t i = 0; i < done; i++) {
        erase_unit(d, u0 + i);
    }
    if (partial) {
        if (ctl->mode == FAKE_FLASH_CUT_DURING || ctl->mode == FAKE_FLASH_CUT_WEAK) {
            uint32_t last = d->cfg.tear_scatter ? n : done + 1;
            for (uint32_t i = done; i < last; i++) {
                tear_erase_unit(d, u0 + i, ctl->mode == FAKE_FLASH_CUT_WEAK);
            }
        }
        cut_now(op);
    }
    if (hit && ctl->mode == FAKE_FLASH_CUT_AFTER) {
        cut_now(op);
    }
    return 0;
}

#if !defined(FAKE_FLASH_NO_PORT)
int mboot_port_flash_dev_init(void) {
    return 0;
}

int mboot_port_flash_dev_read(uint8_t dev, uint32_t off, void *dst, uint32_t len) {
    return fake_flash_dev_read(dev, off, dst, len);
}

int mboot_port_flash_dev_write(uint8_t dev, uint32_t off, const void *src, uint32_t len) {
    return fake_flash_dev_write(dev, off, src, len);
}

int mboot_port_flash_dev_erase(uint8_t dev, uint32_t off, uint32_t len) {
    return fake_flash_dev_erase(dev, off, len);
}
#endif

uint32_t mboot_port_ecc_events(void) {
    return ctl->ecc_events;
}

uint32_t mboot_port_ecc_corrected(void) {
    return ctl->corrected_events;
}

void mboot_port_ecc_clear(void) {
    // The fake controller has no sticky flag; events are only counted.
}
