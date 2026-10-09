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

#ifndef MICROPY_INCLUDED_SHARED_MBOOT_MBOOT_RING_H
#define MICROPY_INCLUDED_SHARED_MBOOT_MBOOT_RING_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "flash_map_backend/flash_map_backend.h"

// Fixed size records appended to a flash area of erase units, such as the flash security counter.
// A record starts with a magic word and carries a CRC-32 of its first
// crc_span bytes in the word that follows them; the rest of the slot is padding. A slot holds
// one record of max(record size, write unit) bytes and is written with a single flash write, so
// a slot torn by a power cut is either unreadable or fails its CRC and never reads as valid.
// The backends decide which valid record is the current one and where the next one goes.

#define MBOOT_RING_SLOT_MAX (32)

typedef enum {
    MBOOT_RING_SLOT_FREE,         // all bytes erased
    MBOOT_RING_SLOT_VALID,        // magic and CRC match
    MBOOT_RING_SLOT_USED,         // readable, not erased, and not a valid record
    MBOOT_RING_SLOT_UNREADABLE,   // the read failed with -EIO: an invalid or corrected word
} mboot_ring_slot_state_t;

typedef struct {
    const struct flash_area *fa;
    uint8_t erased;
    uint32_t eu;            // erase unit
    uint32_t units;         // erase units of the ring
    uint32_t slots;         // records per unit
    uint32_t slot_size;     // bytes per slot
    uint32_t magic;
    uint32_t crc_span;
} mboot_ring_t;

// True when all n bytes are the erased value.
bool mboot_is_erased(const void *p, size_t n, uint8_t erased);

// Opens the ring in area_id. rec_len is the length of a record including its CRC word. The ring
// uses the first max_units erase units of the area, or all of them when max_units is 0, and
// needs at least two. Returns 0 or a negative errno (-EINVAL when the geometry does not fit).
int mboot_ring_open(mboot_ring_t *r, uint8_t area_id, uint32_t rec_len, uint32_t magic, uint32_t crc_span, uint32_t max_units);

// Reads one slot into rec, which holds MBOOT_RING_SLOT_MAX bytes, and classifies it. Returns
// 0 or a negative errno other than the -EIO that makes the slot UNREADABLE.
int mboot_ring_slot_read(const mboot_ring_t *r, uint32_t unit, uint32_t slot, void *rec, mboot_ring_slot_state_t *state);

// Calls visit for every slot, unit by unit. rec is only meaningful for a VALID or USED slot.
typedef void (*mboot_ring_visit_t)(void *ctx, uint32_t unit, uint32_t slot, mboot_ring_slot_state_t state, const void *rec);
int mboot_ring_walk(const mboot_ring_t *r, mboot_ring_visit_t visit, void *ctx);

// First free slot of a unit, or r->slots when the unit is full.
int mboot_ring_unit_first_free(const mboot_ring_t *r, uint32_t unit, uint32_t *slot);

// Stores the CRC of the first crc_span bytes of rec at crc_span.
void mboot_ring_seal(const mboot_ring_t *r, void *rec);

// Writes the sealed record of len bytes into a slot, padded with the erased value. A slot beyond
// the last one of the unit means that the unit is full: the next unit of the ring is erased and
// the record becomes its first.
int mboot_ring_put(const mboot_ring_t *r, uint32_t unit, uint32_t slot, const void *rec, uint32_t len);

#endif // MICROPY_INCLUDED_SHARED_MBOOT_MBOOT_RING_H
