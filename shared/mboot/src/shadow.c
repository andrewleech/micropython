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

// ECC flash policy: a power cut inside the program of one write unit leaves a word with an
// invalid ECC. bootutil has no notion of that, so the trailer sectors of the slots on the ECC
// device, which hold its swap state, are kept twice and reads map unreadable words to a defined
// value. A slot on a device without ECC (a SPI flash) has no shadow words. See mboot_shadow.h
// for the scheme.

#if defined(MBOOT_ECC_SHADOW)

#include <errno.h>
#include <stdbool.h>
#include <string.h>

#include "mcuboot_config/mcuboot_logging.h"
#include "flash_map_backend/flash_map_backend.h"
#include "sysflash/sysflash.h"
#include "mboot_port.h"
#include "mboot_ring.h"
#include "mboot_shadow.h"

#define UNIT_MAX (32)

// Size of the image header (struct image_header of bootutil). bootutil reads the header at the
// start of a slot, and of an offset swap's secondary slot also one sector further, before it
// looks at the swap state.
#define HEADER_BYTES (32)

typedef struct {
    bool valid;
    uint8_t dev;
    uint8_t erased;
    uint32_t wu;            // write unit: the granule of ECC and of shadowing
    uint32_t eu;            // erase unit
    uint32_t nslots;        // slots on the device: the primary slot, and the secondary slot if it has ECC
    uint32_t nsec;          // protected sectors per slot
    uint32_t slot[2];       // device offset of the primary / secondary slot
    uint32_t prot[2];       // device offset of the first protected sector of primary / secondary
    uint32_t shadow;        // device offset of the shadow area
} layout_t;

static layout_t layout;

static bool overlaps(const struct flash_area *a, const struct flash_area *b) {
    return a->fa_off < b->fa_off + b->fa_size && b->fa_off < a->fa_off + a->fa_size;
}

static bool layout_compute(layout_t *l) {
    const struct flash_area *pri = mboot_flash_area_find(FLASH_AREA_IMAGE_PRIMARY(0));
    const struct flash_area *sec = mboot_flash_area_find(FLASH_AREA_IMAGE_SECONDARY(0));
    const struct flash_area *sh = mboot_flash_area_find(MBOOT_AREA_SHADOW);
    // The single policy has one slot: the secondary slot id is the primary slot.
    if (pri == NULL || sec == NULL || sh == NULL || (pri == sec) != MBOOT_POLICY_SINGLE) {
        return false;
    }
    // A secondary slot on a device without ECC (a SPI flash) has no shadow words.
    uint32_t nslots = 1 + MBOOT_SECONDARY_ECC;
    if (pri->fa_device_id != sh->fa_device_id || pri->fa_device_id >= mboot_dev_count
        || (nslots == 2 && pri->fa_device_id != sec->fa_device_id)) {
        return false;
    }
    const mboot_flash_dev_t *d = &mboot_devs[pri->fa_device_id];
    // The slots and the shadow area work on sectors of one erase unit.
    uint32_t eu = mboot_area_erase(pri);
    if (d->write_unit == 0 || d->write_unit > UNIT_MAX || eu == 0 || eu % d->write_unit != 0
        || mboot_area_erase(sh) != eu || (nslots == 2 && mboot_area_erase(sec) != eu)) {
        return false;
    }
    // The shadow area has one sector for every protected sector of the slots.
    if (sh->fa_size % (nslots * eu) != 0 || sh->fa_size == 0) {
        return false;
    }
    uint32_t nsec = sh->fa_size / (nslots * eu);
    if (nsec > pri->fa_size / eu || (nslots == 2 && nsec > sec->fa_size / eu)) {
        return false;
    }
    if (overlaps(sh, pri) || (nslots == 2 && overlaps(sh, sec))) {
        return false;
    }
    l->dev = pri->fa_device_id;
    l->erased = d->erased_val;
    l->wu = d->write_unit;
    l->eu = eu;
    l->nslots = nslots;
    l->nsec = nsec;
    l->slot[0] = pri->fa_off;
    l->prot[0] = pri->fa_off + pri->fa_size - nsec * eu;
    if (nslots == 2) {
        l->slot[1] = sec->fa_off;
        l->prot[1] = sec->fa_off + sec->fa_size - nsec * eu;
    }
    l->shadow = sh->fa_off;
    l->valid = true;
    return true;
}

static const layout_t *layout_get(void) {
    if (!layout.valid) {
        layout_compute(&layout);
    }
    return layout.valid ? &layout : NULL;
}

int mboot_shadow_layout_check(void) {
    layout.valid = false;
    return layout_get() != NULL ? 0 : -EINVAL;
}

// Device offset of the shadow of the protected word at off, or false when off is not in a
// protected sector.
static bool prot_shadow(const layout_t *l, uint8_t dev, uint32_t off, uint32_t *shadow_off) {
    if (dev != l->dev) {
        return false;
    }
    uint32_t span = l->nsec * l->eu;
    for (unsigned s = 0; s < l->nslots; s++) {
        if (off >= l->prot[s] && off - l->prot[s] < span) {
            *shadow_off = l->shadow + s * span + (off - l->prot[s]);
            return true;
        }
    }
    return false;
}

// True when the unit at off is part of an image header window: the first HEADER_BYTES of the
// first and of the second sector of a slot.
static bool header_unit(const layout_t *l, uint8_t dev, uint32_t off) {
    if (dev != l->dev) {
        return false;
    }
    for (unsigned s = 0; s < l->nslots; s++) {
        for (unsigned k = 0; k < 2; k++) {
            uint32_t start = l->slot[s] + k * l->eu;
            if (off >= start && off < start + HEADER_BYTES) {
                return true;
            }
        }
    }
    return false;
}

// First protected offset above pos, or end when there is none below end.
static uint32_t next_prot(const layout_t *l, uint8_t dev, uint32_t pos, uint32_t end) {
    if (dev != l->dev) {
        return end;
    }
    uint32_t next = end;
    for (unsigned s = 0; s < l->nslots; s++) {
        if (l->prot[s] > pos && l->prot[s] < next) {
            next = l->prot[s];
        }
    }
    return next;
}

// Reads one write unit. *invalid is set when the unit cannot be trusted: its ECC is invalid, or
// the controller corrected it. A power cut inside a program leaves a word that reads either way,
// and a corrected word can carry data that was never written (a corrected 0x01 flag has been
// seen to read 0xEB). *corrected_only is set when the unit was only corrected: its data is then
// as good as any data the controller corrects. Only failures other than those return a negative
// errno. The port reports the invalid unit through the event counter, through -EIO, or both.
static int unit_read(const layout_t *l, uint32_t off, uint8_t *buf, bool *invalid, bool *corrected_only) {
    uint32_t e0 = mboot_port_ecc_events();
    uint32_t c0 = mboot_port_ecc_corrected();
    int rc = mboot_flash_raw_read(l->dev, off, buf, l->wu);
    uint32_t e1 = mboot_port_ecc_events();
    uint32_t c1 = mboot_port_ecc_corrected();
    bool uncorrectable = rc == -EIO || (rc == 0 && e1 != e0);
    if (uncorrectable || (rc == 0 && c1 != c0)) {
        mboot_port_ecc_clear();
        *invalid = true;
        if (corrected_only != NULL) {
            *corrected_only = !uncorrectable;
        }
        return 0;
    }
    *invalid = false;
    return rc;
}

// ---- reads ----

int mboot_shadow_read(uint8_t dev, uint32_t off, void *dst, uint32_t len) {
    uint32_t e0 = mboot_port_ecc_events();
    uint32_t c0 = mboot_port_ecc_corrected();
    int rc = mboot_flash_raw_read(dev, off, dst, len);
    if (rc == 0 && mboot_port_ecc_events() == e0 && mboot_port_ecc_corrected() == c0) {
        return 0;
    }
    if (rc != 0 && rc != -EIO) {
        return rc;
    }

    // At least one word in the range has an invalid ECC. Resolve it word by word.
    mboot_port_ecc_clear();
    const layout_t *l = layout_get();
    if (l == NULL || dev != l->dev) {
        return -EIO;
    }
    uint8_t unit[UNIT_MAX];
    uint8_t shad[UNIT_MAX];
    uint32_t end = off + len;
    for (uint32_t pos = off & ~(l->wu - 1); pos < end; pos += l->wu) {
        bool inv;
        bool corrected;
        rc = unit_read(l, pos, unit, &inv, &corrected);
        if (rc != 0) {
            return rc;
        }
        if (inv) {
            uint32_t soff;
            if (!prot_shadow(l, dev, pos, &soff)) {
                if (corrected) {
                    // Corrected data outside the protected sectors is returned as it is:
                    // the hash of the image covers it.
                    goto copy;
                }
                if (header_unit(l, dev, pos)) {
                    // A cut inside the copy of a sector leaves an invalid unit where the image
                    // header goes. bootutil has to be able to read the header of the slot to
                    // resume the swap, and the sector is copied again, so the unit reads as
                    // erased: no image header.
                    memset(unit, l->erased, l->wu);
                    MCUBOOT_LOG_WRN("header word at 0x%x unreadable, reading as erased", (unsigned)pos);
                    goto copy;
                }
                // Other data: the caller must treat it as unreadable.
                return -EIO;
            }
            bool sinv;
            rc = unit_read(l, soff, shad, &sinv, NULL);
            if (rc != 0) {
                return rc;
            }
            if (!sinv && !mboot_is_erased(shad, l->wu, l->erased)) {
                memcpy(unit, shad, l->wu);
                MCUBOOT_LOG_WRN("word at 0x%x unreadable, using the shadow", (unsigned)pos);
            } else {
                memset(unit, l->erased, l->wu);
                MCUBOOT_LOG_WRN("word at 0x%x unreadable, reading as erased", (unsigned)pos);
            }
        }
    copy:;
        uint32_t a = pos > off ? pos : off;
        uint32_t b = pos + l->wu < end ? pos + l->wu : end;
        memcpy((uint8_t *)dst + (a - off), unit + (a - pos), b - a);
    }
    return 0;
}

// ---- writes ----

// Programs one protected word and its shadow. The primary word is programmed only when it
// reads erased; a word that is already written or unreadable is never programmed again
// because a second program of a written word leaves an invalid ECC. The shadow word is
// programmed only when it reads erased, with the value that the primary word will read as.
static int write_protected_unit(const layout_t *l, uint32_t poff, uint32_t soff, const uint8_t *data) {
    uint8_t cur[UNIT_MAX];
    uint8_t shad[UNIT_MAX];
    bool inv;
    bool sinv;

    if (mboot_is_erased(data, l->wu, l->erased)) {
        return 0;
    }
    int rc = unit_read(l, poff, cur, &inv, NULL);
    if (rc != 0) {
        return rc;
    }
    const uint8_t *value;
    if (!inv && mboot_is_erased(cur, l->wu, l->erased)) {
        rc = mboot_flash_raw_write(l->dev, poff, data, l->wu);
        if (rc != 0) {
            return rc;
        }
        value = data;
    } else if (inv) {
        value = data;
    } else {
        if (memcmp(cur, data, l->wu) != 0) {
            MCUBOOT_LOG_WRN("word at 0x%x already written with other data", (unsigned)poff);
        }
        value = cur;
    }

    rc = unit_read(l, soff, shad, &sinv, NULL);
    if (rc != 0) {
        return rc;
    }
    if (!sinv && mboot_is_erased(shad, l->wu, l->erased)) {
        return mboot_flash_raw_write(l->dev, soff, value, l->wu);
    }
    return 0;
}

int mboot_shadow_write(uint8_t dev, uint32_t off, const void *src, uint32_t len) {
    if (dev >= mboot_dev_count) {
        return -ENODEV;
    }
    const layout_t *l = layout_get();
    if (l == NULL) {
        return -EINVAL;
    }
    const mboot_flash_dev_t *d = &mboot_devs[dev];
    if (off > d->size || len > d->size - off || (off % d->write_unit) != 0 || (len % d->write_unit) != 0) {
        return -EINVAL;
    }

    const uint8_t *p = src;
    uint32_t pos = off;
    uint32_t end = off + len;
    while (pos < end) {
        uint32_t soff;
        uint32_t step;
        int rc;
        if (prot_shadow(l, dev, pos, &soff)) {
            step = l->wu;
            rc = write_protected_unit(l, pos, soff, p);
        } else {
            step = next_prot(l, dev, pos, end) - pos;
            rc = mboot_flash_raw_write(dev, pos, p, step);
        }
        if (rc != 0) {
            return rc;
        }
        pos += step;
        p += step;
    }
    return 0;
}

// ---- erases ----

int mboot_shadow_erase(uint8_t dev, uint32_t off, uint32_t len) {
    if (dev >= mboot_dev_count) {
        return -ENODEV;
    }
    const layout_t *l = layout_get();
    if (l == NULL) {
        return -EINVAL;
    }
    if (!mboot_dev_erase_range_ok(&mboot_devs[dev], off, len)) {
        return -EINVAL;
    }

    uint32_t pos = off;
    uint32_t end = off + len;
    while (pos < end) {
        uint32_t soff;
        int rc;
        if (prot_shadow(l, dev, pos, &soff)) {
            // Primary sector first: a cut between the two erases reads as erased.
            rc = mboot_flash_raw_erase(dev, pos, l->eu);
            if (rc == 0) {
                rc = mboot_flash_raw_erase(dev, soff, l->eu);
            }
            pos += l->eu;
        } else {
            uint32_t next = next_prot(l, dev, pos, end);
            rc = mboot_flash_raw_erase(dev, pos, next - pos);
            pos = next;
        }
        if (rc != 0) {
            return rc;
        }
    }
    return 0;
}

// ---- startup scrub ----

int mboot_shadow_scrub(void) {
    const layout_t *l = layout_get();
    if (l == NULL) {
        return -EINVAL;
    }
    uint8_t p[UNIT_MAX];
    uint8_t s[UNIT_MAX];
    uint32_t units = l->eu / l->wu;

    for (uint32_t k = 0; k < l->nslots * l->nsec; k++) {
        uint32_t poff = l->prot[k / l->nsec] + (k % l->nsec) * l->eu;
        uint32_t soff = l->shadow + k * l->eu;

        // Classify the sector pair. A word that reads erased in the primary sector while its
        // shadow word is written can only come from an erase of the primary sector that
        // did not finish (the erase runs primary first and a written pair is always
        // programmed primary first), so the sector holds a mixture of erased and old words.
        bool p_blank = true;
        bool s_blank = true;
        bool erase_cut = false;
        for (uint32_t u = 0; u < units; u++) {
            bool pinv;
            bool sinv;
            int rc = unit_read(l, poff + u * l->wu, p, &pinv, NULL);
            if (rc != 0) {
                return rc;
            }
            bool p_erased = !pinv && mboot_is_erased(p, l->wu, l->erased);
            rc = unit_read(l, soff + u * l->wu, s, &sinv, NULL);
            if (rc != 0) {
                return rc;
            }
            bool s_erased = !sinv && mboot_is_erased(s, l->wu, l->erased);
            p_blank = p_blank && p_erased;
            s_blank = s_blank && s_erased;
            erase_cut = erase_cut || (p_erased && !s_erased);
        }

        if (erase_cut) {
            // Finish the erase: the primary sector, then its shadow, as an erase does.
            MCUBOOT_LOG_INF("finishing the interrupted erase of trailer sector %u", (unsigned)k);
            int rc = 0;
            if (!p_blank) {
                rc = mboot_flash_raw_erase(l->dev, poff, l->eu);
            }
            if (rc == 0) {
                rc = mboot_flash_raw_erase(l->dev, soff, l->eu);
            }
            if (rc != 0) {
                return rc;
            }
            continue;
        }

        for (uint32_t u = 0; u < units; u++) {
            bool pinv;
            bool sinv;
            int rc = unit_read(l, poff + u * l->wu, p, &pinv, NULL);
            if (rc != 0) {
                return rc;
            }
            if (pinv || mboot_is_erased(p, l->wu, l->erased)) {
                continue;
            }
            rc = unit_read(l, soff + u * l->wu, s, &sinv, NULL);
            if (rc != 0) {
                return rc;
            }
            if (sinv) {
                continue;
            }
            if (mboot_is_erased(s, l->wu, l->erased)) {
                // The shadow write of a completed primary write was cut.
                rc = mboot_flash_raw_write(l->dev, soff + u * l->wu, p, l->wu);
                if (rc != 0) {
                    return rc;
                }
            } else if (memcmp(p, s, l->wu) != 0) {
                MCUBOOT_LOG_WRN("shadow word at 0x%x differs from the primary word", (unsigned)(poff + u * l->wu));
            }
        }
    }
    return 0;
}

#endif // MBOOT_ECC_SHADOW
