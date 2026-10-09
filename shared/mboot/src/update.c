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
#include <stddef.h>

#include "bootutil/bootutil_public.h"
#include "bootutil/image.h"
#include "flash_map_backend/flash_map_backend.h"
#include "mcuboot_config/mcuboot_config.h"
#include "mboot_port.h"
#include "mboot_update.h"
#include "sysflash/sysflash.h"

int mboot_update_target(mboot_update_target_t *t) {
    const struct flash_area *sec;
    if (flash_area_open(FLASH_AREA_IMAGE_SECONDARY(0), &sec) != 0) {
        return -ENODEV;
    }
    uint32_t trailer_off;
    if (mboot_flash_slot_trailer_off(sec, &trailer_off) != 0) {
        return -ENODEV;
    }
    uint32_t update_off = 0;
    #if defined(MCUBOOT_SWAP_USING_OFFSET)
    update_off = mboot_area_erase(sec);
    #endif
    if (trailer_off <= update_off) {
        return -ENODEV;
    }
    t->fap = sec;
    t->update_off = update_off;
    t->trailer_off = trailer_off;
    t->capacity = trailer_off - update_off;
    if (t->capacity > MBOOT_MAX_IMAGE_SIZE) {
        t->capacity = MBOOT_MAX_IMAGE_SIZE;
    }
    return 0;
}

int mboot_update_begin(const mboot_update_target_t *t, uint32_t *fail_off) {
    *fail_off = 0;
    #if MBOOT_POLICY_SINGLE
    // The only slot has no swap state and no spare sector; its sectors are erased as they are written.
    (void)t;
    return 0;
    #else
    const struct flash_area *fap = t->fap;
    uint32_t erase = mboot_area_erase(fap);
    for (uint32_t end = fap->fa_size; end > t->trailer_off; end -= erase) {
        MCUBOOT_WATCHDOG_FEED();
        int rc = flash_area_erase(fap, end - erase, erase);
        if (rc != 0) {
            *fail_off = fap->fa_off + end - erase;
            return rc;
        }
    }
    for (uint32_t off = 0; off < t->update_off; off += erase) {
        MCUBOOT_WATCHDOG_FEED();
        int rc = flash_area_erase(fap, off, erase);
        if (rc != 0) {
            *fail_off = fap->fa_off + off;
            return rc;
        }
    }
    return 0;
    #endif
}

#if defined(MBOOT_ROLE_BOOTLOADER)

int mboot_update_mark_pending(bool permanent) {
    #if MBOOT_POLICY_SINGLE
    (void)permanent;
    return 0;
    #else
    #if MBOOT_POLICY_OVERWRITE_EXTERNAL
    permanent = true;
    #endif
    const struct flash_area *pri;
    uint32_t magic = 0;
    if (flash_area_open(FLASH_AREA_IMAGE_PRIMARY(0), &pri) != 0 || flash_area_read(pri, 0, &magic, sizeof(magic)) != 0) {
        return -EIO;
    }
    #if defined(MCUBOOT_BOOTSTRAP)
    if (magic != IMAGE_MAGIC) {
        return 0;
    }
    #endif
    return boot_set_pending_multi(0, permanent ? 1 : 0);
    #endif
}

#endif
