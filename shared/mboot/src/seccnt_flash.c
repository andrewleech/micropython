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

#include "mcuboot_config/mcuboot_config.h"

#if defined(MBOOT_SECCNT_FLASH)

#include <errno.h>
#include <stdbool.h>
#include <string.h>

#include "flash_map_backend/flash_map_backend.h"
#include "sysflash/sysflash.h"
#include "mboot_ring.h"
#include "mboot_seccnt.h"

// Two erase units of records, each carrying an image id and a value. The counter of an image is
// the largest value of its valid records in either unit; a value of 0 needs no record. A unit is
// complete when it holds a valid record and the largest value of every image.
//
// A new record is appended only when exactly one unit is complete and it has a free slot after
// its last used one. Otherwise the units switch: the complete unit with more used slots (unit 0
// on a tie) is kept, the other unit is erased completely, then receives a copy of the largest
// value of every image and the new record last.
//
// A unit is erased only while the other unit is complete, so a power cut never loses or lowers a
// counter, and a torn record is skipped because its CRC or its ECC fails. A unit whose erase was
// cut is never written: if it is incomplete the other unit is the only complete one, and if it
// still looks complete both are, and the next record starts with a switch that erases one of
// them completely. Records only go to slots behind the last used slot of a unit that was erased
// completely before its first record.
//
// Only a fully erased area may start counting at 0 (the first boot). An area that is not erased
// and holds no valid record has lost its counter or is damaged: init, read, write and the update
// check fail with -EIO, and the caller must not take that for a low counter. The one such state
// that is not damage is a power cut inside the very first record: unit 0 holds only its first
// slot, that slot is unreadable or holds only bits of the first record, and unit 1 is erased.
// Init erases unit 0 and stores the first record again.

#define REC_PAYLOAD (16)    // magic, image id, value, crc32
#define RING_UNITS (2)
#define IDS_MAX (4)         // images with a non-zero counter that the area keeps

typedef struct {
    uint32_t magic;
    uint32_t image_id;
    uint32_t value;
    uint32_t crc32;
} seccnt_rec_t;

// A switch writes a copy for every image and the new record into the started unit, so a unit
// needs a slot more than the area keeps images.
static int ring_open(mboot_ring_t *r) {
    int rc = mboot_ring_open(r, MBOOT_AREA_SECCNT, REC_PAYLOAD, MBOOT_SECCNT_REC_MAGIC, REC_PAYLOAD - sizeof(uint32_t), RING_UNITS);
    if (rc == 0 && r->slots < IDS_MAX + 1) {
        rc = -EINVAL;
    }
    return rc;
}

// The bytes of a record as they are stored: padded to the slot size with the erased value.
static void rec_encode(const mboot_ring_t *r, uint8_t *buf, uint32_t image_id, uint32_t value) {
    seccnt_rec_t rec = {MBOOT_SECCNT_REC_MAGIC, image_id, value, 0};
    mboot_ring_seal(r, &rec);
    memset(buf, r->erased, r->slot_size);
    memcpy(buf, &rec, sizeof(rec));
}

// True when every bit that is programmed in the slot is programmed in the record too: what a
// write of the record leaves when power fails inside it.
static bool is_partial_program(const uint8_t *slot, const uint8_t *rec, size_t n, uint8_t erased) {
    for (size_t i = 0; i < n; i++) {
        if (((slot[i] ^ erased) & ~(rec[i] ^ erased) & 0xFF) != 0) {
            return false;
        }
    }
    return true;
}

// The largest value of one image, when it is not 0, and the units that hold a record of it.
typedef struct {
    uint32_t id;
    uint32_t max;
    uint8_t units;          // bit per unit
} id_max_t;

typedef struct {
    const mboot_ring_t *ring;
    uint8_t first[MBOOT_RING_SLOT_MAX];   // the first record, as stored
    bool any;               // a valid record exists
    bool too_many;          // non-zero values of more than IDS_MAX images
    size_t n_ids;
    id_max_t ids[IDS_MAX];
    uint32_t next[RING_UNITS];      // per unit, the slot after the last one that is not erased
    bool valid_in[RING_UNITS];      // per unit, it holds a valid record
    bool blank;             // every slot of both units is erased
    uint32_t used;          // slots that are not erased
    bool first_torn;        // unit 0 slot 0 is unreadable or holds only bits of the first record
} scan_t;

static id_max_t *find_id(scan_t *sc, uint32_t image_id) {
    for (size_t i = 0; i < sc->n_ids; i++) {
        if (sc->ids[i].id == image_id) {
            return &sc->ids[i];
        }
    }
    return NULL;
}

static void scan_visit(void *ctx, uint32_t unit, uint32_t slot, mboot_ring_slot_state_t st, const void *buf) {
    scan_t *sc = ctx;
    if (st != MBOOT_RING_SLOT_FREE) {
        sc->blank = false;
        sc->used++;
        sc->next[unit] = slot + 1;
    }
    if (unit == 0 && slot == 0) {
        sc->first_torn = st == MBOOT_RING_SLOT_UNREADABLE
            || (st == MBOOT_RING_SLOT_USED && is_partial_program(buf, sc->first, sc->ring->slot_size, sc->ring->erased));
    }
    if (st != MBOOT_RING_SLOT_VALID) {
        return;
    }
    seccnt_rec_t rec;
    memcpy(&rec, buf, sizeof(rec));
    sc->any = true;
    sc->valid_in[unit] = true;
    if (rec.value == 0) {
        // 0 is also the counter of an image without a record: it needs no entry.
        return;
    }
    id_max_t *e = find_id(sc, rec.image_id);
    if (e == NULL) {
        if (sc->n_ids == IDS_MAX) {
            sc->too_many = true;
            return;
        }
        e = &sc->ids[sc->n_ids++];
        e->id = rec.image_id;
        e->max = rec.value;
        e->units = 1u << unit;
    } else if (rec.value > e->max) {
        e->max = rec.value;
        e->units = 1u << unit;
    } else if (rec.value == e->max) {
        e->units |= 1u << unit;
    }
}

static int scan(const mboot_ring_t *r, scan_t *sc) {
    memset(sc, 0, sizeof(*sc));
    sc->ring = r;
    sc->blank = true;
    rec_encode(r, sc->first, 0, 0);
    return mboot_ring_walk(r, scan_visit, sc);
}

// scan() for the uses of the counter: an area without a valid record is only usable when it is
// erased, and non-zero values of more images than the area keeps are damage.
static int scan_usable(const mboot_ring_t *r, scan_t *sc) {
    int rc = scan(r, sc);
    if (rc == 0 && ((!sc->any && !sc->blank) || sc->too_many)) {
        return -EIO;
    }
    return rc;
}

static bool unit_complete(const scan_t *sc, uint32_t unit) {
    if (!sc->valid_in[unit]) {
        return false;
    }
    for (size_t i = 0; i < sc->n_ids; i++) {
        if (!(sc->ids[i].units & (1u << unit))) {
            return false;
        }
    }
    return true;
}

static int put(const mboot_ring_t *r, uint32_t unit, uint32_t slot, uint32_t image_id, uint32_t value) {
    uint8_t buf[MBOOT_RING_SLOT_MAX];
    rec_encode(r, buf, image_id, value);
    return mboot_ring_put(r, unit, slot, buf, r->slot_size);
}

// Stores a new largest value of image_id.
static int store(const mboot_ring_t *r, const scan_t *sc, uint32_t image_id, uint32_t value) {
    if (sc->blank) {
        return put(r, 0, 0, image_id, value);
    }

    uint32_t n_complete = 0;
    uint32_t keep = 0;
    for (uint32_t u = 0; u < RING_UNITS; u++) {
        if (unit_complete(sc, u)) {
            if (n_complete == 0 || sc->next[u] > sc->next[keep]) {
                keep = u;
            }
            n_complete++;
        }
    }
    if (n_complete == 0) {
        return -EIO;
    }
    if (n_complete == 1 && sc->next[keep] < r->slots) {
        return put(r, keep, sc->next[keep], image_id, value);
    }

    // Switch. The copies and the record fit: ring_open() checked the slots of a unit.
    uint32_t unit = (keep + 1) % RING_UNITS;
    int rc = flash_area_erase(r->fa, unit * r->eu, r->eu);
    if (rc != 0) {
        return rc;
    }
    uint32_t slot = 0;
    for (size_t i = 0; i < sc->n_ids; i++) {
        rc = put(r, unit, slot++, sc->ids[i].id, sc->ids[i].max);
        if (rc != 0) {
            return rc;
        }
    }
    return put(r, unit, slot, image_id, value);
}

int mboot_seccnt_init(void) {
    mboot_ring_t r;
    scan_t sc;
    int rc = ring_open(&r);
    if (rc == 0) {
        rc = scan(&r, &sc);
    }
    if (rc != 0) {
        return rc;
    }
    if (sc.any) {
        return 0;
    }
    if (sc.blank) {
        return put(&r, 0, 0, 0, 0);
    }
    if (sc.used == 1 && sc.first_torn) {
        rc = flash_area_erase(r.fa, 0, r.eu);
        if (rc != 0) {
            return rc;
        }
        return put(&r, 0, 0, 0, 0);
    }
    return -EIO;
}

int mboot_seccnt_flash_read(uint32_t image_id, uint32_t *value) {
    mboot_ring_t r;
    scan_t sc;
    int rc = ring_open(&r);
    if (rc == 0) {
        rc = scan_usable(&r, &sc);
    }
    if (rc != 0) {
        return rc;
    }
    const id_max_t *e = find_id(&sc, image_id);
    *value = e != NULL ? e->max : 0;
    return 0;
}

int mboot_seccnt_flash_write(uint32_t image_id, uint32_t value) {
    mboot_ring_t r;
    scan_t sc;
    int rc = ring_open(&r);
    if (rc == 0) {
        rc = scan_usable(&r, &sc);
    }
    if (rc != 0) {
        return rc;
    }
    const id_max_t *e = find_id(&sc, image_id);
    if (e != NULL ? value <= e->max : value == 0) {
        return 0;
    }
    if (e == NULL && sc.n_ids == IDS_MAX) {
        return -ENOSPC;
    }
    return store(&r, &sc, image_id, value);
}

bool mboot_seccnt_flash_can_update(uint32_t image_id, uint32_t value) {
    mboot_ring_t r;
    scan_t sc;
    return ring_open(&r) == 0 && scan_usable(&r, &sc) == 0
           && (value == 0 || find_id(&sc, image_id) != NULL || sc.n_ids < IDS_MAX);
}

#endif // MBOOT_SECCNT_FLASH
