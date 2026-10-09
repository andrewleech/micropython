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

// Two erase units of records. The record with the largest value is the counter. Appending goes
// to the unit that holds the largest value. When it is full, the other unit (which never holds
// the only copy of the maximum) is erased and gets the record first. A power cut therefore
// never loses the previous maximum, and a torn record is skipped because its CRC or its ECC
// fails.
//
// Only a fully erased area may start counting at 0 (the first boot). An area that is not erased
// and holds no valid record has lost its counter or is damaged: init, read, write and the update
// check fail with -EIO, and the caller must not take that for a low counter. The one such state
// that is not damage is a power cut inside the very first record: unit 0 holds only its first
// slot, that slot is unreadable or holds only bits of the first record, and unit 1 is erased.
// Init erases unit 0 and stores the first record again.

#define REC_PAYLOAD (16)    // magic, image id, value, crc32
#define RING_UNITS (2)

typedef struct {
    uint32_t magic;
    uint32_t image_id;
    uint32_t value;
    uint32_t crc32;
} seccnt_rec_t;

static int ring_open(mboot_ring_t *r) {
    return mboot_ring_open(r, MBOOT_AREA_SECCNT, REC_PAYLOAD, MBOOT_SECCNT_REC_MAGIC, REC_PAYLOAD - sizeof(uint32_t), RING_UNITS);
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

typedef struct {
    const mboot_ring_t *ring;
    uint32_t image_id;      // the image whose value is wanted
    uint8_t first[MBOOT_RING_SLOT_MAX];   // the first record, as stored
    bool any;               // a valid record exists
    uint32_t max_any;       // largest value of any image
    uint32_t active;        // unit holding max_any
    bool has_image;
    uint32_t max_image;     // largest value of the requested image
    bool blank;             // every slot of both units is erased
    uint32_t used;          // slots that are not erased
    bool first_torn;        // unit 0 slot 0 is unreadable or holds only bits of the first record
} scan_t;

static void scan_visit(void *ctx, uint32_t unit, uint32_t slot, mboot_ring_slot_state_t st, const void *buf) {
    scan_t *sc = ctx;
    if (st != MBOOT_RING_SLOT_FREE) {
        sc->blank = false;
        sc->used++;
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
    if (!sc->any || rec.value > sc->max_any) {
        sc->any = true;
        sc->max_any = rec.value;
        sc->active = unit;
    }
    if (rec.image_id == sc->image_id && (!sc->has_image || rec.value > sc->max_image)) {
        sc->has_image = true;
        sc->max_image = rec.value;
    }
}

static int scan(const mboot_ring_t *r, uint32_t image_id, scan_t *sc) {
    memset(sc, 0, sizeof(*sc));
    sc->ring = r;
    sc->image_id = image_id;
    sc->blank = true;
    rec_encode(r, sc->first, 0, 0);
    return mboot_ring_walk(r, scan_visit, sc);
}

// scan() for the uses of the counter: an area without a valid record is only usable when it is
// erased.
static int scan_usable(const mboot_ring_t *r, uint32_t image_id, scan_t *sc) {
    int rc = scan(r, image_id, sc);
    if (rc == 0 && !sc->any && !sc->blank) {
        return -EIO;
    }
    return rc;
}

static int append(const mboot_ring_t *r, const scan_t *sc, uint32_t image_id, uint32_t value) {
    uint32_t slot = 0;
    int rc = mboot_ring_unit_first_free(r, sc->active, &slot);
    if (rc != 0) {
        return rc;
    }
    uint8_t buf[MBOOT_RING_SLOT_MAX];
    rec_encode(r, buf, image_id, value);
    return mboot_ring_put(r, sc->active, slot, buf, r->slot_size);
}

int mboot_seccnt_init(void) {
    mboot_ring_t r;
    scan_t sc;
    int rc = ring_open(&r);
    if (rc == 0) {
        rc = scan(&r, 0, &sc);
    }
    if (rc != 0) {
        return rc;
    }
    if (sc.any) {
        return 0;
    }
    if (sc.blank) {
        return append(&r, &sc, 0, 0);
    }
    if (sc.used == 1 && sc.first_torn) {
        rc = flash_area_erase(r.fa, 0, r.eu);
        if (rc != 0) {
            return rc;
        }
        return append(&r, &sc, 0, 0);
    }
    return -EIO;
}

int mboot_seccnt_flash_read(uint32_t image_id, uint32_t *value) {
    mboot_ring_t r;
    scan_t sc;
    int rc = ring_open(&r);
    if (rc == 0) {
        rc = scan_usable(&r, image_id, &sc);
    }
    if (rc != 0) {
        return rc;
    }
    *value = sc.has_image ? sc.max_image : 0;
    return 0;
}

int mboot_seccnt_flash_write(uint32_t image_id, uint32_t value) {
    mboot_ring_t r;
    scan_t sc;
    int rc = ring_open(&r);
    if (rc == 0) {
        rc = scan_usable(&r, image_id, &sc);
    }
    if (rc != 0) {
        return rc;
    }
    if (sc.has_image && value <= sc.max_image) {
        return 0;
    }
    if (!sc.has_image && value == 0) {
        return 0;
    }
    return append(&r, &sc, image_id, value);
}

bool mboot_seccnt_flash_can_update(uint32_t image_id, uint32_t value) {
    (void)value;
    mboot_ring_t r;
    scan_t sc;
    return ring_open(&r) == 0 && scan_usable(&r, image_id, &sc) == 0;
}

#endif // MBOOT_SECCNT_FLASH
