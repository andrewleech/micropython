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

#include "mcuboot_config/mcuboot_config.h"
#include "mcuboot_config/mcuboot_logging.h"
#include "flash_map_backend/flash_map_backend.h"
#include "sysflash/sysflash.h"
#include "mcuboot_log.h"
#include "mcuboot_port.h"
#include "mcuboot_request.h"
#include "mcuboot_shadow.h"

// Alignment bootutil assumes for trailer flags (the default of bootutil when the layout
// header does not raise it).
#if defined(MCUBOOT_BOOT_MAX_ALIGN)
#define BOOT_MAX_ALIGN MCUBOOT_BOOT_MAX_ALIGN
#else
#define BOOT_MAX_ALIGN 8
#endif

#if defined(MCUBOOT_ROLE_BOOTLOADER) && defined(MCUBOOT_FSLOAD_ENABLE) && MCUBOOT_FSLOAD_ENABLE
#define HAVE_STREAM_AREA 1
#else
#define HAVE_STREAM_AREA 0
#endif

// ---- fault injection (test builds only) ----

#if defined(MCUBOOT_TEST_FI)

#define FI_MAGIC 0x4A4E4946u    // bytes "FINJ"
#define FI_MODE_TRACE (0x80u)   // bit 7 of the mode word: print every operation before the hook

typedef struct {
    uint32_t magic;
    uint32_t target;
    uint32_t counter;
    uint32_t mode;
} fi_state_t;

// Counts the operation and calls the port hook around it while the state in the request
// region is armed (magic present). The hook may reset the device instead of returning.
static int fi_run(int op, uint8_t dev, uint32_t off, uint32_t len, const void *src) {
    size_t size = 0;
    volatile fi_state_t *st = (volatile fi_state_t *)((uint8_t *)mcuboot_port_request_ram(&size) + MCUBOOT_FI_STATE_OFFSET);
    bool armed = size >= MCUBOOT_FI_STATE_OFFSET + sizeof(fi_state_t) && st->magic == FI_MAGIC;
    if (armed) {
        st->counter = st->counter + 1;
        if (st->mode & FI_MODE_TRACE) {
            mcuboot_log(MCUBOOT_LOG_LEVEL_ERROR, "FI %u %c %u 0x%x 0x%x\n", (unsigned)st->counter, op == 0 ? 'w' : 'e', (unsigned)dev,
                (unsigned)off, (unsigned)len);
        }
        mcuboot_port_fi_hook(op, dev, off, len, 0);
    }
    int rc = op == 0 ? mcuboot_port_flash_write(dev, off, src, len) : mcuboot_port_flash_erase(dev, off, len);
    if (armed) {
        mcuboot_port_fi_hook(op, dev, off, len, 1);
    }
    return rc;
}

#endif // MCUBOOT_TEST_FI

// ---- device access without ECC policy ----

static const mcuboot_flash_dev_t *dev_get(uint8_t dev) {
    return dev < mcuboot_dev_count ? &mcuboot_devs[dev] : NULL;
}

static bool in_device(const mcuboot_flash_dev_t *d, uint32_t off, uint32_t len) {
    return off <= d->size && len <= d->size - off;
}

int mcuboot_flash_raw_read(uint8_t dev, uint32_t off, void *dst, uint32_t len) {
    const mcuboot_flash_dev_t *d = dev_get(dev);
    if (d == NULL) {
        return -ENODEV;
    }
    if (!in_device(d, off, len)) {
        return -EINVAL;
    }
    if (len == 0) {
        return 0;
    }
    return mcuboot_port_flash_read(dev, off, dst, len);
}

int mcuboot_flash_raw_write(uint8_t dev, uint32_t off, const void *src, uint32_t len) {
    const mcuboot_flash_dev_t *d = dev_get(dev);
    if (d == NULL) {
        return -ENODEV;
    }
    if (!in_device(d, off, len) || (off % d->write_unit) != 0 || (len % d->write_unit) != 0) {
        return -EINVAL;
    }
    if (len == 0) {
        return 0;
    }
    #if defined(MCUBOOT_TEST_FI)
    return fi_run(0, dev, off, len, src);
    #else
    return mcuboot_port_flash_write(dev, off, src, len);
    #endif
}

int mcuboot_flash_raw_erase(uint8_t dev, uint32_t off, uint32_t len) {
    const mcuboot_flash_dev_t *d = dev_get(dev);
    if (d == NULL) {
        return -ENODEV;
    }
    if (!in_device(d, off, len) || !mcuboot_dev_erase_range_ok(d, off, len)) {
        return -EINVAL;
    }
    if (len == 0) {
        return 0;
    }
    #if defined(MCUBOOT_TEST_FI)
    return fi_run(1, dev, off, len, NULL);
    #else
    return mcuboot_port_flash_erase(dev, off, len);
    #endif
}

// ---- device access with the flash policy ----

// For ports whose controller has no correction flag.
__attribute__((weak)) uint32_t mcuboot_port_ecc_corrected(void) {
    return 0;
}

int mcuboot_flash_area_read_record(const struct flash_area *fa, uint32_t off, void *dst, uint32_t len) {
    uint32_t c0 = mcuboot_port_ecc_corrected();
    int rc = flash_area_read(fa, off, dst, len);
    return rc == 0 && mcuboot_port_ecc_corrected() != c0 ? -EIO : rc;
}

int mcuboot_flash_dev_read(uint8_t dev, uint32_t off, void *dst, uint32_t len) {
    #if defined(MCUBOOT_ECC_SHADOW)
    return mcuboot_shadow_read(dev, off, dst, len);
    #else
    return mcuboot_flash_raw_read(dev, off, dst, len);
    #endif
}

int mcuboot_flash_dev_write(uint8_t dev, uint32_t off, const void *src, uint32_t len) {
    #if defined(MCUBOOT_ECC_SHADOW)
    return mcuboot_shadow_write(dev, off, src, len);
    #else
    return mcuboot_flash_raw_write(dev, off, src, len);
    #endif
}

int mcuboot_flash_dev_erase(uint8_t dev, uint32_t off, uint32_t len) {
    #if defined(MCUBOOT_ECC_SHADOW)
    return mcuboot_shadow_erase(dev, off, len);
    #else
    return mcuboot_flash_raw_erase(dev, off, len);
    #endif
}

int mcuboot_flash_scrub(void) {
    #if defined(MCUBOOT_ECC_SHADOW)
    return mcuboot_shadow_scrub();
    #else
    return 0;
    #endif
}

// ---- flash map consistency ----

static bool is_pow2(uint32_t v) {
    return v != 0 && (v & (v - 1)) == 0;
}

const struct flash_area *mcuboot_flash_area_find(uint8_t id) {
    for (unsigned i = 0; i < mcuboot_area_count; i++) {
        if (mcuboot_areas[i].fa_id == id) {
            return &mcuboot_areas[i];
        }
    }
    return NULL;
}

// The trailer of a slot occupies whole sectors at its end, as many as the layout needs for the
// swap state (MCUBOOT_TRAILER_SECTORS). This is the only place that computes where it begins.
_Static_assert(MCUBOOT_PRIMARY_SIZE > MCUBOOT_MAX_IMAGE_SIZE, "the primary slot holds a trailer");

uint32_t mcuboot_area_erase(const struct flash_area *fa) {
    const mcuboot_flash_dev_t *d = fa != NULL ? dev_get(fa->fa_device_id) : NULL;
    return d != NULL ? mcuboot_dev_erase_at(d, fa->fa_off) : 0;
}

int mcuboot_flash_slot_trailer_off(const struct flash_area *fa, uint32_t *trailer_off) {
    uint32_t unit = mcuboot_area_erase(fa);
    if (unit == 0) {
        return -ENOENT;
    }
    #if MCUBOOT_POLICY_SINGLE
    // The only slot holds no swap state.
    *trailer_off = fa->fa_size;
    return 0;
    #else
    uint32_t sectors = MCUBOOT_TRAILER_SECTORS;
    if (sectors > fa->fa_size / unit) {
        return -EINVAL;
    }
    *trailer_off = fa->fa_size - sectors * unit;
    return 0;
    #endif
}

int mcuboot_flash_erase_trailer(uint8_t area_id) {
    const struct flash_area *fa = mcuboot_flash_area_find(area_id);
    uint32_t trailer_off;
    if (fa == NULL) {
        return -ENOENT;
    }
    int rc = mcuboot_flash_slot_trailer_off(fa, &trailer_off);
    if (rc != 0) {
        return rc;
    }
    return mcuboot_flash_dev_erase(fa->fa_device_id, fa->fa_off + trailer_off, fa->fa_size - trailer_off);
}

// The runs of a device are whole power of two units that start at a multiple of their unit, at
// least the write unit, and add up to the device size.
static bool runs_ok(const mcuboot_flash_dev_t *d) {
    if (d->run_count == 0 || d->run_count > MCUBOOT_MAX_RUNS) {
        return false;
    }
    uint32_t start = 0;
    for (unsigned i = 0; i < d->run_count; i++) {
        const mcuboot_flash_run_t *r = &d->runs[i];
        if (!is_pow2(r->erase) || r->erase < d->write_unit || r->size == 0 || (r->size % r->erase) != 0
            || (start % r->erase) != 0) {
            return false;
        }
        start += r->size;
    }
    return start == d->size;
}

int mcuboot_flash_map_check(void) {
    if (mcuboot_dev_count == 0 || mcuboot_area_count == 0) {
        return -EINVAL;
    }

    for (unsigned i = 0; i < mcuboot_dev_count; i++) {
        const mcuboot_flash_dev_t *d = &mcuboot_devs[i];
        if (d->id != i || !is_pow2(d->write_unit) || !runs_ok(d)) {
            return -EINVAL;
        }
        // An ECC-invalid word at either end still shows that the device answers.
        uint8_t probe[32];
        if (d->write_unit > sizeof(probe)) {
            return -EINVAL;
        }
        int rc = mcuboot_flash_raw_read(i, 0, probe, d->write_unit);
        if (rc != 0 && rc != -EIO) {
            return rc;
        }
        rc = mcuboot_flash_raw_read(i, d->size - d->write_unit, probe, d->write_unit);
        if (rc != 0 && rc != -EIO) {
            return rc;
        }
    }

    for (unsigned i = 0; i < mcuboot_area_count; i++) {
        const struct flash_area *fa = &mcuboot_areas[i];
        const mcuboot_flash_dev_t *d = dev_get(fa->fa_device_id);
        uint32_t unit = d != NULL ? mcuboot_dev_erase_at(d, fa->fa_off) : 0;
        if (d == NULL || fa->fa_size == 0 || !in_device(d, fa->fa_off, fa->fa_size) || unit == 0
            || (fa->fa_off % unit) != 0 || (fa->fa_size % unit) != 0
            || fa->fa_off + fa->fa_size > mcuboot_dev_run_end(d, fa->fa_off)) {
            return -EINVAL;
        }
        for (unsigned j = 0; j < i; j++) {
            if (mcuboot_areas[j].fa_id == fa->fa_id) {
                return -EINVAL;
            }
        }
    }

    // bootutil places every trailer flag at a multiple of BOOT_MAX_ALIGN, so that value must
    // be a multiple of the write unit of the devices that hold slots.
    const struct flash_area *slots[2] = {mcuboot_flash_area_find(FLASH_AREA_IMAGE_PRIMARY(0)), mcuboot_flash_area_find(FLASH_AREA_IMAGE_SECONDARY(0))};
    for (unsigned i = 0; i < 2; i++) {
        if (slots[i] != NULL) {
            const mcuboot_flash_dev_t *d = dev_get(slots[i]->fa_device_id);
            if (BOOT_MAX_ALIGN < d->write_unit || (BOOT_MAX_ALIGN % d->write_unit) != 0) {
                return -EINVAL;
            }
            #if defined(MCUBOOT_ECC_SHADOW)
            if (BOOT_MAX_ALIGN != d->write_unit) {
                return -EINVAL;
            }
            #endif
        }
    }

    #if defined(MCUBOOT_ECC_SHADOW)
    return mcuboot_shadow_layout_check();
    #else
    return 0;
    #endif
}

// ---- bootutil flash_area API ----

int flash_area_open(uint8_t id, const struct flash_area **fa) {
    *fa = mcuboot_flash_area_find(id);
    return *fa != NULL ? 0 : -ENOENT;
}

void flash_area_close(const struct flash_area *fa) {
    (void)fa;
}

static bool in_area(const struct flash_area *fa, uint32_t off, uint32_t len) {
    return fa != NULL && off <= fa->fa_size && len <= fa->fa_size - off;
}

int flash_area_read(const struct flash_area *fa, uint32_t off, void *dst, uint32_t len) {
    if (!in_area(fa, off, len)) {
        return -EINVAL;
    }
    if (fa->fa_device_id == MCUBOOT_DEV_STREAM) {
        #if HAVE_STREAM_AREA
        return mcuboot_stream_area_read(off, dst, len);
        #else
        return -ENODEV;
        #endif
    }
    return mcuboot_flash_dev_read(fa->fa_device_id, fa->fa_off + off, dst, len);
}

int flash_area_write(const struct flash_area *fa, uint32_t off, const void *src, uint32_t len) {
    if (!in_area(fa, off, len)) {
        return -EINVAL;
    }
    if (fa->fa_device_id == MCUBOOT_DEV_STREAM) {
        return -EACCES;
    }
    return mcuboot_flash_dev_write(fa->fa_device_id, fa->fa_off + off, src, len);
}

int flash_area_erase(const struct flash_area *fa, uint32_t off, uint32_t len) {
    if (!in_area(fa, off, len)) {
        return -EINVAL;
    }
    if (fa->fa_device_id == MCUBOOT_DEV_STREAM) {
        return -EACCES;
    }
    return mcuboot_flash_dev_erase(fa->fa_device_id, fa->fa_off + off, len);
}

uint32_t flash_area_align(const struct flash_area *fa) {
    if (fa->fa_device_id == MCUBOOT_DEV_STREAM) {
        return 1;
    }
    return mcuboot_devs[fa->fa_device_id].write_unit;
}

uint8_t flash_area_erased_val(const struct flash_area *fa) {
    if (fa->fa_device_id == MCUBOOT_DEV_STREAM) {
        return 0xFF;
    }
    return mcuboot_devs[fa->fa_device_id].erased_val;
}

int flash_area_get_sectors(int fa_id, uint32_t *count, struct flash_sector *sectors) {
    const struct flash_area *fa = mcuboot_flash_area_find((uint8_t)fa_id);
    if (fa == NULL) {
        return -ENOENT;
    }
    uint32_t erase = mcuboot_area_erase(fa);
    if (erase == 0) {
        return -ENODEV;
    }
    uint32_t n = fa->fa_size / erase;
    if (n > *count) {
        return -ENOMEM;
    }
    for (uint32_t i = 0; i < n; i++) {
        sectors[i].fs_off = i * erase;
        sectors[i].fs_size = erase;
    }
    *count = n;
    return 0;
}

int flash_area_get_sector(const struct flash_area *fa, off_t off, struct flash_sector *fs) {
    if (fa->fa_device_id == MCUBOOT_DEV_STREAM) {
        return -ENOTSUP;
    }
    if (off < 0 || (uint32_t)off >= fa->fa_size) {
        return -ERANGE;
    }
    uint32_t erase = mcuboot_area_erase(fa);
    if (erase == 0) {
        return -ENODEV;
    }
    fs->fs_off = ((uint32_t)off / erase) * erase;
    fs->fs_size = erase;
    return 0;
}

int flash_area_id_from_multi_image_slot(int image_index, int slot) {
    if (image_index != 0) {
        return -EINVAL;
    }
    switch (slot) {
        case 0:
            return FLASH_AREA_IMAGE_PRIMARY(0);
        case 1:
            return FLASH_AREA_IMAGE_SECONDARY(0);
    }
    return -EINVAL;
}

int flash_area_id_from_image_slot(int slot) {
    return flash_area_id_from_multi_image_slot(0, slot);
}

int flash_device_base(uint8_t fd_id, uintptr_t *ret) {
    const mcuboot_flash_dev_t *d = dev_get(fd_id);
    if (d == NULL) {
        return -ENODEV;
    }
    *ret = d->base;
    return 0;
}
