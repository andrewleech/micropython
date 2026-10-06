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
#include <stdbool.h>
#include <string.h>

#include "flash_map_backend/flash_map_backend.h"
#include "sysflash/sysflash.h"
#include "mcuboot_ring.h"
#include "mcuboot_updatelog.h"

// The log area is a ring of erase units. Records are appended to the active unit. When it is
// full the next unit is erased and becomes active, so older units keep the history (with two
// units this is a ping-pong pair). A record is written with a single call. A record torn by a
// power cut is either unreadable or fails its CRC, so it never reads as valid, and its slot is
// treated as used.

#define REC_SIZE (sizeof(mcuboot_log_rec_t))

static int ring_open(mcuboot_ring_t *r) {
    return mcuboot_ring_open(r, MCUBOOT_AREA_LOG, REC_SIZE, MCUBOOT_LOG_REC_MAGIC, REC_SIZE - sizeof(uint32_t), 0);
}

typedef struct {
    bool any;               // at least one valid record
    uint32_t newest_seq;
    uint32_t active;        // unit that holds the newest record
} scan_t;

static void scan_visit(void *ctx, uint32_t unit, uint32_t slot, mcuboot_ring_slot_state_t st, const void *buf) {
    scan_t *sc = ctx;
    if (st != MCUBOOT_RING_SLOT_VALID) {
        return;
    }
    mcuboot_log_rec_t rec;
    memcpy(&rec, buf, sizeof(rec));
    if (!sc->any || (int32_t)(rec.seq - sc->newest_seq) > 0) {
        sc->any = true;
        sc->newest_seq = rec.seq;
        sc->active = unit;
    }
}

// Finds the newest valid record of the whole log.
static int ring_scan(const mcuboot_ring_t *r, scan_t *sc) {
    sc->any = false;
    sc->newest_seq = 0;
    sc->active = 0;
    return mcuboot_ring_walk(r, scan_visit, sc);
}

int mcuboot_updatelog_append(uint8_t type, uint8_t result, uint8_t source, const mcuboot_image_info_t *info, uint32_t detail) {
    mcuboot_ring_t r;
    scan_t sc;
    int rc = ring_open(&r);
    if (rc == 0) {
        rc = ring_scan(&r, &sc);
    }
    if (rc != 0) {
        return rc;
    }

    uint32_t unit = sc.active;
    uint32_t slot = 0;
    if (!sc.any) {
        // No valid record: use the first unit that still has a free slot.
        bool found = false;
        for (uint32_t u = 0; u < r.units && !found; u++) {
            rc = mcuboot_ring_unit_first_free(&r, u, &slot);
            if (rc != 0) {
                return rc;
            }
            if (slot < r.slots) {
                unit = u;
                found = true;
            }
        }
        if (!found) {
            unit = 0;
            slot = r.slots;
        }
    } else {
        rc = mcuboot_ring_unit_first_free(&r, unit, &slot);
        if (rc != 0) {
            return rc;
        }
    }

    mcuboot_log_rec_t rec;
    memset(&rec, 0, sizeof(rec));
    rec.magic = MCUBOOT_LOG_REC_MAGIC;
    rec.seq = sc.any ? sc.newest_seq + 1 : 1;
    rec.type = type;
    rec.result = result;
    rec.source = source;
    if (info != NULL && info->valid) {
        rec.ver_major = info->ver_major;
        rec.ver_minor = info->ver_minor;
        rec.ver_rev = info->ver_rev;
        rec.ver_build = info->ver_build;
        rec.hash_prefix = info->hash_prefix;
    }
    rec.detail = detail;
    mcuboot_ring_seal(&r, &rec);
    // A full active unit makes the next unit active: it is erased first.
    return mcuboot_ring_put(&r, unit, slot, &rec, REC_SIZE);
}

int mcuboot_updatelog_read(uint32_t n, mcuboot_log_rec_t *out) {
    mcuboot_ring_t r;
    scan_t sc;
    int rc = ring_open(&r);
    if (rc == 0) {
        rc = ring_scan(&r, &sc);
    }
    if (rc != 0) {
        return rc;
    }
    if (!sc.any) {
        return -ENOENT;
    }

    // Newest first: the active unit backwards, then the units before it in the ring.
    for (uint32_t step = 0; step < r.units; step++) {
        uint32_t u = (sc.active + r.units - step) % r.units;
        for (uint32_t i = r.slots; i-- > 0;) {
            uint8_t buf[MCUBOOT_RING_SLOT_MAX];
            mcuboot_ring_slot_state_t st;
            rc = mcuboot_ring_slot_read(&r, u, i, buf, &st);
            if (rc != 0) {
                return rc;
            }
            if (st == MCUBOOT_RING_SLOT_VALID) {
                if (n == 0) {
                    memcpy(out, buf, sizeof(*out));
                    return 0;
                }
                n--;
            }
        }
    }
    return -ENOENT;
}
