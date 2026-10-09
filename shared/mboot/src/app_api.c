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

// Application wrappers around bootutil_public.c and the request handoff.
// Compiled into the application build only (MBOOT_ROLE_APP). No signature or hash validation
// happens here; that is done by the bootloader at the next boot.

#include <errno.h>
#include <string.h>

#include "mcuboot_config/mcuboot_config.h"
#include "sysflash/sysflash.h"
#include "flash_map_backend/flash_map_backend.h"
#include "bootutil/image.h"
#include "bootutil/bootutil_public.h"

#include "mboot_port.h"
#include "mboot_types.h"
#include "mboot_request.h"
#include "mboot_update.h"
#include "mboot_app.h"

_Static_assert(BOOT_SWAP_TYPE_NONE == MBOOT_APP_SWAP_NONE, "swap type values");
_Static_assert(BOOT_SWAP_TYPE_TEST == MBOOT_APP_SWAP_TEST, "swap type values");
_Static_assert(BOOT_SWAP_TYPE_PERM == MBOOT_APP_SWAP_PERM, "swap type values");
_Static_assert(BOOT_SWAP_TYPE_REVERT == MBOOT_APP_SWAP_REVERT, "swap type values");
_Static_assert(BOOT_SWAP_TYPE_FAIL == MBOOT_APP_SWAP_FAIL, "swap type values");
_Static_assert(MBOOT_APP_ELEMS_MAX == MBOOT_REQ_ELEMS_MAX, "element stream limit");
_Static_assert(MBOOT_APP_WRITER_CHUNK % 32 == 0, "writer chunk is a multiple of every write unit");

// Info block in the last 64 bytes of the bootloader area, as placed by the bootloader build
// (ports/stm32/mboot/mcuboot/main.c).
#define BL_INFO_SIZE        64
#define BL_INFO_MAGIC       0x4E49424Du
#define BL_INFO_VERSION     1

typedef struct {
    uint32_t magic;
    uint16_t info_version;
    uint16_t flags;
    uint32_t layout_id;
    uint32_t api_version;
    char version[24];
    uint8_t reserved[24];
} bl_info_raw_t;

_Static_assert(sizeof(bl_info_raw_t) == BL_INFO_SIZE, "bootloader info block size");

enum {
    WRITER_OPEN = 1,
    WRITER_FINISHED,
    WRITER_ABORTED,
    WRITER_FAILED,
};

// Space before the update image in the secondary slot (swap using offset).
#if MBOOT_POLICY_SWAP && defined(MCUBOOT_SWAP_USING_OFFSET)
#define UPDATE_SPARE_SECTORS 1
#else
#define UPDATE_SPARE_SECTORS 0
#endif

#if MBOOT_POLICY_SINGLE
#define HAVE_SECONDARY 0
#else
#define HAVE_SECONDARY 1
#endif

static int s_map_checked;

// ---- helpers ----

static int check_map(void) {
    if (!s_map_checked) {
        if (mboot_flash_map_check() != 0) {
            return -ENODEV;
        }
        s_map_checked = 1;
    }
    return 0;
}

static int read_bl_info(bl_info_raw_t *raw) {
    const struct flash_area *fap;
    if (flash_area_open(FLASH_AREA_BOOTLOADER, &fap) != 0) {
        return -ENOENT;
    }
    uint32_t size = flash_area_get_size(fap);
    int rc = -ENOENT;
    if (size >= BL_INFO_SIZE && flash_area_read(fap, size - BL_INFO_SIZE, raw, BL_INFO_SIZE) == 0
        && raw->magic == BL_INFO_MAGIC && raw->info_version == BL_INFO_VERSION) {
        raw->version[sizeof(raw->version) - 1] = '\0';
        rc = 0;
    }
    flash_area_close(fap);
    return rc;
}

// 0 when the layout of the running application matches the bootloader info block or the info
// block is absent, -ENODEV when the layout ids differ.
static int mboot_app_check_layout(void) {
    bl_info_raw_t raw;
    if (read_bl_info(&raw) != 0) {
        return 0;
    }
    return raw.layout_id == (uint32_t)MBOOT_LAYOUT_ID ? 0 : -ENODEV;
}

#if HAVE_SECONDARY
static int check_ready(void) {
    int rc = check_map();
    return rc != 0 ? rc : mboot_app_check_layout();
}
#endif

static uint32_t update_offset(const struct flash_area *sec) {
    return UPDATE_SPARE_SECTORS ? mboot_area_erase(sec) : 0;
}

// Reads the image header of a slot. 0 if a bootable image header is present, -ENOENT if not,
// -EIO on a flash error.
static int read_header(unsigned slot, struct image_header *hdr) {
    const struct flash_area *fap;
    uint32_t off = 0;
    int id = FLASH_AREA_IMAGE_PRIMARY(0);
    if (slot == MBOOT_APP_SLOT_SECONDARY) {
        #if HAVE_SECONDARY
        id = FLASH_AREA_IMAGE_SECONDARY(0);
        #else
        return -ENOENT;
        #endif
    }
    if (flash_area_open(id, &fap) != 0) {
        return -EIO;
    }
    if (slot == MBOOT_APP_SLOT_SECONDARY) {
        off = update_offset(fap);
    }
    int rc = flash_area_read(fap, off, hdr, sizeof(*hdr));
    flash_area_close(fap);
    if (rc != 0) {
        return -EIO;
    }
    if (hdr->ih_magic != IMAGE_MAGIC || (hdr->ih_flags & IMAGE_F_NON_BOOTABLE)) {
        return -ENOENT;
    }
    return 0;
}

static void version_from_header(const struct image_header *hdr, mboot_app_version_t *v) {
    v->major = hdr->ih_ver.iv_major;
    v->minor = hdr->ih_ver.iv_minor;
    v->revision = hdr->ih_ver.iv_revision;
    v->build = hdr->ih_ver.iv_build_num;
}


static int read_primary_state(struct boot_swap_state *st) {
    if (boot_read_swap_state_by_id(FLASH_AREA_IMAGE_PRIMARY(0), st) != 0) {
        return -EIO;
    }
    return 0;
}

// ---- queries ----

int mboot_app_version(unsigned slot, mboot_app_version_t *version) {
    if (slot != MBOOT_APP_SLOT_PRIMARY && slot != MBOOT_APP_SLOT_SECONDARY) {
        return -EINVAL;
    }
    struct image_header hdr;
    int rc = read_header(slot, &hdr);
    if (rc == 0) {
        version_from_header(&hdr, version);
    }
    return rc;
}

int mboot_app_bootloader_info(mboot_app_bl_info_t *info) {
    bl_info_raw_t raw;
    int rc = read_bl_info(&raw);
    if (rc != 0) {
        return rc;
    }
    memcpy(info->version, raw.version, sizeof(raw.version));
    info->version[sizeof(raw.version)] = '\0';
    info->layout_id = raw.layout_id;
    info->api = raw.api_version;
    return 0;
}

int mboot_app_state(mboot_app_state_t *state) {
    int rc = check_map();
    if (rc != 0) {
        return rc;
    }
    memset(state, 0, sizeof(*state));
    state->layout_mismatch = mboot_app_check_layout() != 0;

    struct boot_swap_state primary;
    rc = read_primary_state(&primary);
    if (rc != 0) {
        return rc;
    }

    #if HAVE_SECONDARY
    int swap = boot_swap_type_multi(0);
    if (swap < BOOT_SWAP_TYPE_NONE || swap > BOOT_SWAP_TYPE_FAIL) {
        return -EIO;
    }
    state->swap = swap;
    state->confirmed = primary.image_ok == BOOT_FLAG_SET;
    state->pending = swap == BOOT_SWAP_TYPE_TEST || swap == BOOT_SWAP_TYPE_PERM || swap == BOOT_SWAP_TYPE_REVERT;
    struct image_header hdr;
    rc = read_header(MBOOT_APP_SLOT_SECONDARY, &hdr);
    if (rc != 0 && rc != -ENOENT) {
        return rc;
    }
    state->secondary_valid_header = rc == 0;
    #else
    // A single slot has no trailer state: the running image is final.
    state->swap = MBOOT_APP_SWAP_NONE;
    state->confirmed = true;
    #endif
    return 0;
}

size_t mboot_app_slot_count(void) {
    return mboot_area_count;
}

static const char *area_name(uint8_t id) {
    switch (id) {
        case FLASH_AREA_BOOTLOADER:
            return "boot";
        case FLASH_AREA_IMAGE_PRIMARY(0):
            return "primary";
        #if HAVE_SECONDARY
        case FLASH_AREA_IMAGE_SECONDARY(0):
            return "secondary";
        #endif
        case FLASH_AREA_IMAGE_SCRATCH:
            return "scratch";
        case MBOOT_AREA_SECCNT:
            return "seccnt";
        case MBOOT_AREA_FS:
            return "fs";
        case MBOOT_AREA_INTENT:
            return "intent";
        case MBOOT_AREA_SHADOW:
            return "shadow";
        default:
            return "area";
    }
}

int mboot_app_slot_get(size_t index, mboot_app_slot_t *slot) {
    if (index >= mboot_area_count) {
        return -ENOENT;
    }
    const struct flash_area *fa = &mboot_areas[index];
    slot->name = area_name(fa->fa_id);
    slot->start = mboot_devs[fa->fa_device_id].base + fa->fa_off;
    slot->size = fa->fa_size;
    return 0;
}

int mboot_app_fs_area(uint32_t *base, uint32_t *len) {
    for (unsigned i = 0; i < mboot_area_count; ++i) {
        const struct flash_area *fa = &mboot_areas[i];
        if (fa->fa_id == MBOOT_AREA_FS) {
            *base = mboot_devs[fa->fa_device_id].base + fa->fa_off;
            *len = fa->fa_size;
            return 0;
        }
    }
    return -ENOENT;
}


// ---- actions ----

int mboot_app_confirm(void) {
    #if HAVE_SECONDARY
    struct boot_swap_state before;
    int rc = read_primary_state(&before);
    if (rc != 0) {
        return rc;
    }
    if (boot_set_confirmed() != 0) {
        return -EIO;
    }
    #endif
    return 0;
}

int mboot_app_confirm_if_pending(void) {
    #if HAVE_SECONDARY
    struct boot_swap_state st;
    int rc = read_primary_state(&st);
    if (rc != 0) {
        return rc;
    }
    if (st.magic == BOOT_MAGIC_GOOD && st.image_ok != BOOT_FLAG_SET) {
        return mboot_app_confirm();
    }
    #endif
    return 0;
}

static int set_pending(bool permanent) {
    #if HAVE_SECONDARY
    int rc = check_ready();
    if (rc != 0) {
        return rc;
    }
    struct image_header hdr;
    rc = read_header(MBOOT_APP_SLOT_SECONDARY, &hdr);
    if (rc == -ENOENT) {
        return -EINVAL;
    } else if (rc != 0) {
        return rc;
    }
    if (boot_set_pending(permanent ? 1 : 0) != 0) {
        return -EIO;
    }
    return 0;
    #else
    (void)permanent;
    return -EPERM;
    #endif
}

int mboot_app_request_upgrade(bool permanent) {
    return set_pending(permanent);
}

void mboot_app_reset(void) {
    mboot_request_clear();
    mboot_port_reset();
}

void mboot_app_request_dfu(void) {
    mboot_request_set_and_reset(MBOOT_REQ_DFU, NULL, 0);
}

// Returns the number of bytes up to and including the END element, or -EINVAL.
static int elems_extent(const uint8_t *elems, size_t len) {
    if (elems == NULL || len > MBOOT_APP_ELEMS_MAX) {
        return -EINVAL;
    }
    size_t pos = 0;
    while (len - pos >= 2) {
        uint8_t type = elems[pos];
        size_t elem_len = elems[pos + 1];
        if (elem_len > len - pos - 2) {
            return -EINVAL;
        }
        pos += 2 + elem_len;
        if (type == 1) {
            // END has length zero.
            return elem_len == 0 ? (int)pos : -EINVAL;
        }
    }
    return -EINVAL;
}

int mboot_app_request_fsload(const uint8_t *elems, size_t len) {
    int extent = elems_extent(elems, len);
    if (extent < 0) {
        return -EINVAL;
    }
    mboot_request_set_and_reset(MBOOT_REQ_FSLOAD, elems, (size_t)extent);
}

// ---- Writer ----

int mboot_app_writer_open(mboot_app_writer_t *w, bool permanent) {
    #if HAVE_SECONDARY
    memset(w, 0, sizeof(*w));
    int rc = check_ready();
    if (rc != 0) {
        return rc;
    }

    mboot_update_target_t t;
    if (mboot_update_target(&t) != 0) {
        return -ENODEV;
    }
    const struct flash_area *sec = t.fap;
    const mboot_flash_dev_t *dev = &mboot_devs[flash_area_get_device_id(sec)];
    if (dev->write_unit == 0 || dev->write_unit > 32) {
        return -ENODEV;
    }

    w->permanent = permanent;
    w->write_unit = dev->write_unit;
    w->erased_val = flash_area_erased_val(sec);
    w->erase_unit = mboot_area_erase(sec);
    w->update_off = t.update_off;
    w->trailer_off = t.trailer_off;
    w->capacity = t.capacity;

    // Clear any stale pending state first, then the spare sector, so no earlier image can be
    // combined with the new data.
    uint32_t fail_off;
    rc = mboot_update_begin(&t, &fail_off);
    if (rc != 0) {
        w->state = WRITER_FAILED;
        return -EIO;
    }
    w->state = WRITER_OPEN;
    return 0;
    #else
    (void)w;
    (void)permanent;
    return -EPERM;
    #endif
}

#if HAVE_SECONDARY
// Writes the buffered bytes (padded to the write unit), erasing sectors ahead of the write.
static int writer_flush(mboot_app_writer_t *w) {
    if (w->fill == 0) {
        return 0;
    }
    uint8_t *chunk = (uint8_t *)w->chunk;
    uint32_t n = (w->fill + w->write_unit - 1) / w->write_unit * w->write_unit;
    memset(chunk + w->fill, w->erased_val, n - w->fill);

    const struct flash_area *sec;
    if (flash_area_open(FLASH_AREA_IMAGE_SECONDARY(0), &sec) != 0) {
        return -EIO;
    }
    int rc = 0;
    uint32_t end = w->flushed + n;
    while (w->erased_to < end) {
        uint32_t off = w->update_off + w->erased_to;
        if (off >= w->trailer_off) {
            // The trailer sectors were erased when the writer was opened.
            w->erased_to = end;
            break;
        }
        rc = flash_area_erase(sec, off, w->erase_unit);
        if (rc != 0) {
            break;
        }
        w->erased_to += w->erase_unit;
    }
    if (rc == 0) {
        rc = flash_area_write(sec, w->update_off + w->flushed, chunk, n);
    }
    flash_area_close(sec);
    if (rc != 0) {
        return -EIO;
    }
    w->flushed += n;
    w->fill = 0;
    return 0;
}
#endif

int mboot_app_writer_write(mboot_app_writer_t *w, const uint8_t *buf, size_t len) {
    #if HAVE_SECONDARY
    if (w->state == WRITER_FAILED) {
        return -EIO;
    } else if (w->state != WRITER_OPEN) {
        return -EBADF;
    }
    if (len > w->capacity - w->total) {
        return -ENOSPC;
    }
    uint8_t *chunk = (uint8_t *)w->chunk;
    while (len > 0) {
        size_t n = MBOOT_APP_WRITER_CHUNK - w->fill;
        if (n > len) {
            n = len;
        }
        memcpy(chunk + w->fill, buf, n);
        w->fill += n;
        w->total += n;
        buf += n;
        len -= n;
        if (w->fill == MBOOT_APP_WRITER_CHUNK) {
            int rc = writer_flush(w);
            if (rc != 0) {
                w->state = WRITER_FAILED;
                return rc;
            }
        }
    }
    return 0;
    #else
    (void)w;
    (void)buf;
    (void)len;
    return -EPERM;
    #endif
}

int mboot_app_writer_finish(mboot_app_writer_t *w, uint32_t *total) {
    #if HAVE_SECONDARY
    if (w->state == WRITER_FAILED) {
        return -EIO;
    } else if (w->state != WRITER_OPEN) {
        return -EBADF;
    }
    #if MBOOT_MIN_IMAGE_SIZE != 0
    if (w->total < MBOOT_MIN_IMAGE_SIZE) {
        // An image that the bootloader would refuse is not marked pending. What was written is
        // erased and the writer is closed, as after any other finish that returns an error.
        w->state = WRITER_FINISHED;
        const struct flash_area *sec;
        if (flash_area_open(FLASH_AREA_IMAGE_SECONDARY(0), &sec) != 0 || flash_area_erase(sec, w->update_off, w->erase_unit) != 0) {
            return -EIO;
        }
        return -EINVAL;
    }
    #endif
    int rc = writer_flush(w);
    if (rc != 0) {
        w->state = WRITER_FAILED;
        return rc;
    }
    w->state = WRITER_FINISHED;
    rc = set_pending(w->permanent);
    if (rc != 0) {
        return rc;
    }
    *total = w->total;
    return 0;
    #else
    (void)w;
    (void)total;
    return -EPERM;
    #endif
}

int mboot_app_writer_abort(mboot_app_writer_t *w) {
    #if HAVE_SECONDARY
    if (w->state == WRITER_ABORTED) {
        return 0;
    } else if (w->state == WRITER_FINISHED) {
        return -EBADF;
    }
    const struct flash_area *sec;
    if (flash_area_open(FLASH_AREA_IMAGE_SECONDARY(0), &sec) != 0) {
        return -EIO;
    }
    int rc = flash_area_erase(sec, w->update_off, w->erase_unit);
    flash_area_close(sec);
    if (rc != 0) {
        w->state = WRITER_FAILED;
        return -EIO;
    }
    w->state = WRITER_ABORTED;
    return 0;
    #else
    (void)w;
    return -EPERM;
    #endif
}
