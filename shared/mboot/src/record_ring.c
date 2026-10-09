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
#include <string.h>

#include "flash_map_backend/flash_map_backend.h"
#include "sysflash/sysflash.h"
#include "mboot_crc32.h"
#include "mboot_port.h"
#include "mboot_ring.h"

bool mboot_is_erased(const void *p, size_t n, uint8_t erased) {
    const uint8_t *b = p;
    for (size_t i = 0; i < n; i++) {
        if (b[i] != erased) {
            return false;
        }
    }
    return true;
}

int mboot_ring_open(mboot_ring_t *r, uint8_t area_id, uint32_t rec_len, uint32_t magic, uint32_t crc_span, uint32_t max_units) {
    int rc = flash_area_open(area_id, &r->fa);
    if (rc != 0) {
        return rc;
    }
    const mboot_flash_dev_t *d = &mboot_devs[r->fa->fa_device_id];
    r->erased = d->erased_val;
    r->eu = mboot_area_erase(r->fa);
    if (r->eu == 0) {
        return -EINVAL;
    }
    r->slot_size = d->write_unit > rec_len ? d->write_unit : rec_len;
    r->slots = r->eu / r->slot_size;
    r->units = r->fa->fa_size / r->eu;
    if (max_units != 0 && r->units > max_units) {
        r->units = max_units;
    }
    r->magic = magic;
    r->crc_span = crc_span;
    if (r->units < 2 || r->slots == 0 || r->slot_size > MBOOT_RING_SLOT_MAX || crc_span + sizeof(uint32_t) > rec_len) {
        return -EINVAL;
    }
    return 0;
}

int mboot_ring_slot_read(const mboot_ring_t *r, uint32_t unit, uint32_t slot, void *rec, mboot_ring_slot_state_t *state) {
    uint8_t *b = rec;
    int rc = mboot_flash_area_read_record(r->fa, unit * r->eu + slot * r->slot_size, b, r->slot_size);
    if (rc == -EIO) {
        *state = MBOOT_RING_SLOT_UNREADABLE;
        return 0;
    }
    if (rc != 0) {
        return rc;
    }
    uint32_t magic;
    uint32_t crc;
    memcpy(&magic, b, sizeof(magic));
    memcpy(&crc, b + r->crc_span, sizeof(crc));
    if (mboot_is_erased(b, r->slot_size, r->erased)) {
        *state = MBOOT_RING_SLOT_FREE;
    } else if (magic == r->magic && crc == mboot_crc32(0, b, r->crc_span)) {
        *state = MBOOT_RING_SLOT_VALID;
    } else {
        *state = MBOOT_RING_SLOT_USED;
    }
    return 0;
}

int mboot_ring_walk(const mboot_ring_t *r, mboot_ring_visit_t visit, void *ctx) {
    uint8_t rec[MBOOT_RING_SLOT_MAX];
    for (uint32_t u = 0; u < r->units; u++) {
        for (uint32_t i = 0; i < r->slots; i++) {
            mboot_ring_slot_state_t st;
            int rc = mboot_ring_slot_read(r, u, i, rec, &st);
            if (rc != 0) {
                return rc;
            }
            visit(ctx, u, i, st, rec);
        }
    }
    return 0;
}

int mboot_ring_unit_first_free(const mboot_ring_t *r, uint32_t unit, uint32_t *slot) {
    uint8_t rec[MBOOT_RING_SLOT_MAX];
    for (uint32_t i = 0; i < r->slots; i++) {
        mboot_ring_slot_state_t st;
        int rc = mboot_ring_slot_read(r, unit, i, rec, &st);
        if (rc != 0) {
            return rc;
        }
        if (st == MBOOT_RING_SLOT_FREE) {
            *slot = i;
            return 0;
        }
    }
    *slot = r->slots;
    return 0;
}

void mboot_ring_seal(const mboot_ring_t *r, void *rec) {
    uint32_t crc = mboot_crc32(0, rec, r->crc_span);
    memcpy((uint8_t *)rec + r->crc_span, &crc, sizeof(crc));
}

int mboot_ring_put(const mboot_ring_t *r, uint32_t unit, uint32_t slot, const void *rec, uint32_t len) {
    if (slot >= r->slots) {
        unit = (unit + 1) % r->units;
        int rc = flash_area_erase(r->fa, unit * r->eu, r->eu);
        if (rc != 0) {
            return rc;
        }
        slot = 0;
    }
    uint8_t buf[MBOOT_RING_SLOT_MAX];
    memset(buf, r->erased, r->slot_size);
    memcpy(buf, rec, len);
    return flash_area_write(r->fa, unit * r->eu + slot * r->slot_size, buf, r->slot_size);
}
