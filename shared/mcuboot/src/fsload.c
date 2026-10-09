/*
 * This file is part of the MicroPython project, http://micropython.org/
 *
 * The MIT License (MIT)
 *
 * Copyright (c) 2019-2020 Damien P. George
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

#include <string.h>

#include "mcuboot_config/mcuboot_config.h"
#include "mcuboot_config/mcuboot_logging.h"
#include "bootutil/bootutil_public.h"
#include "bootutil/image.h"
#include "flash_map_backend/flash_map_backend.h"
#include "sysflash/sysflash.h"
#include "mboot_elem.h"
#include "mcuboot_crc32.h"
#include "mcuboot_fsload.h"
#include "mcuboot_port.h"
#include "mcuboot_request.h"
#include "mcuboot_types.h"
#include "mcuboot_update.h"
#include "mcuboot_validate.h"

#if defined(MCUBOOT_FSLOAD_ENABLE) && MCUBOOT_FSLOAD_ENABLE

// The request, the filesystem and the file are all untrusted until mcuboot_validate_view()
// has accepted the image, so everything is parsed with checked arithmetic. Offsets and lengths
// are uint32_t throughout. Windows, file size and image length are bounded by device and
// target sizes before use.

#if !(defined(MCUBOOT_FSLOAD_RAW) && MCUBOOT_FSLOAD_RAW) && !(defined(MCUBOOT_FSLOAD_FAT) && MCUBOOT_FSLOAD_FAT) && !(defined(MCUBOOT_FSLOAD_LFS2) && MCUBOOT_FSLOAD_LFS2)
#error "MCUBOOT_FSLOAD_ENABLE needs at least one of MCUBOOT_FSLOAD_RAW, _FAT and _LFS2"
#endif

// Write chunk size of pass 2. A copy erases whole sectors and writes chunks at chunk-aligned
// offsets.
#define FSLOAD_COPY_CHUNK (1024u)

// Intent record of policy single: the verbatim request. A power cut in pass 2 leaves no
// bootable image, and this record restarts the load at the next reset. Layout, all little
// endian: magic (4 bytes), len of the request (2), reserved (2, zero), crc32 (4), then the
// request. The CRC-32 covers the first 8 bytes and the request, not the crc32 field.
#define FSLOAD_INTENT_MAGIC (0x5449424Du) // bytes "MBIT"
#define FSLOAD_INTENT_HEADER (12u)        // magic, len, reserved, crc32

#define FSLOAD_TLV_INFO_SIZE (4u)

typedef struct {
    uint8_t fs_type;
    bool has_status;
    uint32_t status_addr;
    uint32_t block_size;                // littlefs2 block size, 0 for the default
    mcuboot_fsload_window_t win[2];     // [1].len is 0 unless the raw mount has a second segment
    char path[MCUBOOT_FSLOAD_PATH_MAX + 1];
} fsload_request_t;

typedef struct {
    mcuboot_update_target_t upd;
    uint32_t erase;
    uint32_t align;
    uint8_t erased_val;
} fsload_target_t;

static uint8_t fsload_buf[FSLOAD_COPY_CHUNK];

static uint16_t get_le16(const uint8_t *p) {
    return p[0] | (uint16_t)p[1] << 8;
}

static uint32_t get_le32(const uint8_t *p) {
    return p[0] | (uint32_t)p[1] << 8 | (uint32_t)p[2] << 16 | (uint32_t)p[3] << 24;
}

// ---- block device windows ----

int mcuboot_fsload_window_read(const mcuboot_fsload_window_t *w, uint32_t off, void *dst, uint32_t len) {
    if (off > w->len || len > w->len - off) {
        return -MCUBOOT_RES_ERR_FS_READ;
    }
    if (len == 0) {
        return 0;
    }
    if (mcuboot_flash_dev_read(w->dev, w->base + off, dst, len) != 0) {
        return -MCUBOOT_RES_ERR_FS_READ;
    }
    return 0;
}

int mcuboot_fsload_window_from_addr(mcuboot_fsload_window_t *w, uint32_t addr, uint32_t len) {
    if (len == 0) {
        return -MCUBOOT_RES_ERR_REQUEST;
    }
    for (unsigned i = 0; i < mcuboot_dev_count; ++i) {
        const mcuboot_flash_dev_t *d = &mcuboot_devs[i];
        if (addr >= d->base && addr - d->base < d->size && len <= d->size - (addr - d->base)) {
            w->dev = i;
            w->base = addr - d->base;
            w->len = len;
            return 0;
        }
    }
    return -MCUBOOT_RES_ERR_REQUEST;
}

// The source of a load is read while pass 2 erases and writes the target. The area tables hold
// the images, the counter, the log, the shadow words and the retry record, so a window may only
// cover the filesystem area or flash that MCUboot doesn't use.
static int window_check(const mcuboot_fsload_window_t *w) {
    for (unsigned i = 0; i < mcuboot_area_count; ++i) {
        const struct flash_area *fa = &mcuboot_areas[i];
        if (fa->fa_id == MCUBOOT_AREA_FS || fa->fa_device_id != w->dev) {
            continue;
        }
        if (w->base < fa->fa_off + fa->fa_size && fa->fa_off < (uint64_t)w->base + w->len) {
            return -MCUBOOT_RES_ERR_REQUEST;
        }
    }
    return 0;
}

// ---- request ----

// A path is 1..254 printable ASCII bytes and none of its components is "." or "..". The
// readers look names up literally, so a path that tries to climb is refused, not interpreted.
static int check_path(const uint8_t *p, unsigned n) {
    unsigned comp_start = 0;
    for (unsigned i = 0; i <= n; ++i) {
        if (i < n && (p[i] < 0x20 || p[i] > 0x7e)) {
            return -MCUBOOT_RES_ERR_FS_PATH;
        }
        if (i == n || p[i] == '/') {
            unsigned comp_len = i - comp_start;
            if ((comp_len == 1 && p[comp_start] == '.') || (comp_len == 2 && p[comp_start] == '.' && p[comp_start + 1] == '.')) {
                return -MCUBOOT_RES_ERR_FS_PATH;
            }
            comp_start = i + 1;
        }
    }
    return 0;
}

// Fills rq->fs_type and the windows from a MOUNT payload of length ml (already checked to be
// 10, 14 or 18).
static int parse_mount(const uint8_t *m, uint8_t ml, fsload_request_t *rq) {
    uint32_t base = get_le32(m + 2);
    uint32_t len = get_le32(m + 6);
    uint32_t arg2 = ml >= MCUBOOT_FSLOAD_MOUNT_LEN_ARG2 ? get_le32(m + 10) : 0;
    uint32_t arg3 = ml >= MCUBOOT_FSLOAD_MOUNT_LEN_ARG3 ? get_le32(m + 14) : 0;
    int r;

    rq->fs_type = m[1];
    rq->block_size = 0;
    rq->win[1].len = 0;
    switch (rq->fs_type) {
        case MCUBOOT_FSLOAD_FS_FAT:
            #if !(defined(MCUBOOT_FSLOAD_FAT) && MCUBOOT_FSLOAD_FAT)
            return -MCUBOOT_RES_ERR_FS_MOUNT;
            #endif
            break;
        case MCUBOOT_FSLOAD_FS_LFS2:
            #if !(defined(MCUBOOT_FSLOAD_LFS2) && MCUBOOT_FSLOAD_LFS2)
            return -MCUBOOT_RES_ERR_FS_MOUNT;
            #endif
            rq->block_size = arg2;
            break;
        case MCUBOOT_FSLOAD_FS_RAW:
            #if !(defined(MCUBOOT_FSLOAD_RAW) && MCUBOOT_FSLOAD_RAW)
            return -MCUBOOT_RES_ERR_FS_MOUNT;
            #endif
            if (arg3 != 0) {
                r = mcuboot_fsload_window_from_addr(&rq->win[1], arg2, arg3);
                if (r == 0) {
                    r = window_check(&rq->win[1]);
                }
                if (r < 0) {
                    return r;
                }
            }
            break;
        case MCUBOOT_FSLOAD_FS_LFS1:
            return -MCUBOOT_RES_ERR_FS_MOUNT;
        default:
            return -MCUBOOT_RES_ERR_REQUEST;
    }
    r = mcuboot_fsload_window_from_addr(&rq->win[0], base, len);
    return r == 0 ? window_check(&rq->win[0]) : r;
}

static int parse_request(const uint8_t *elems, size_t len, fsload_request_t *rq) {
    memset(rq, 0, sizeof(*rq));
    if (!mboot_elem_validate(elems, len)) {
        return -MCUBOOT_RES_ERR_REQUEST;
    }

    uint8_t n;
    const uint8_t *e = mboot_elem_search(elems, len, MBOOT_ELEM_TYPE_STATUS, &n);
    if (e != NULL) {
        if (n != MCUBOOT_FSLOAD_STATUS_LEN) {
            return -MCUBOOT_RES_ERR_REQUEST;
        }
        rq->has_status = true;
        rq->status_addr = get_le32(e);
    }

    e = mboot_elem_search(elems, len, MBOOT_ELEM_TYPE_FSLOAD, &n);
    if (e == NULL || n < 2) {
        return -MCUBOOT_RES_ERR_REQUEST;
    }
    uint8_t mount_point = e[0];
    unsigned path_len = n - 1;
    const uint8_t *path = e + 1;

    // The first MOUNT element for the mount point wins. Every MOUNT element that is walked
    // over must have a valid length.
    const uint8_t *cur = elems;
    size_t remain = len;
    for (;;) {
        uint8_t ml;
        const uint8_t *m = mboot_elem_search(cur, remain, MBOOT_ELEM_TYPE_MOUNT, &ml);
        if (m == NULL) {
            return -MCUBOOT_RES_ERR_REQUEST;
        }
        if (ml != MCUBOOT_FSLOAD_MOUNT_LEN_MIN && ml != MCUBOOT_FSLOAD_MOUNT_LEN_ARG2 && ml != MCUBOOT_FSLOAD_MOUNT_LEN_ARG3) {
            return -MCUBOOT_RES_ERR_REQUEST;
        }
        if (m[0] == mount_point) {
            int r = parse_mount(m, ml, rq);
            if (r < 0) {
                return r;
            }
            break;
        }
        size_t used = (size_t)(m + ml - cur);
        cur += used;
        remain -= used;
    }

    // The path of a raw mount is ignored but must be present.
    if (rq->fs_type != MCUBOOT_FSLOAD_FS_RAW) {
        int r = check_path(path, path_len);
        if (r < 0) {
            return r;
        }
    }
    memcpy(rq->path, path, path_len);
    rq->path[path_len] = '\0';
    return 0;
}

// ---- target slot ----

static int target_open(fsload_target_t *t) {
    if (mcuboot_update_target(&t->upd) != 0) {
        return MCUBOOT_RES_ERR_LAYOUT;
    }
    t->erase = mcuboot_area_erase(t->upd.fap);
    t->align = flash_area_align(t->upd.fap);
    t->erased_val = flash_area_erased_val(t->upd.fap);
    return 0;
}

// ---- image header and length ----

// Total bytes of the image in the stream: header, image, protected TLVs and the unprotected
// TLV area, from the header and the TLV info record. Reads through the stream area, so it
// is served from the caches once the stream is primed.
static int image_length(uint32_t stream_size, uint32_t *out) {
    uint8_t hdr[IMAGE_HEADER_SIZE];
    uint8_t info[FSLOAD_TLV_INFO_SIZE];

    if (mcuboot_stream_area_read(0, hdr, sizeof(hdr)) != 0) {
        return MCUBOOT_RES_ERR_FS_READ;
    }
    uint32_t off = get_le16(hdr + 8);
    if (get_le32(hdr) != IMAGE_MAGIC || __builtin_add_overflow(off, get_le32(hdr + 12), &off) ||
        __builtin_add_overflow(off, get_le16(hdr + 10), &off) || off > stream_size || stream_size - off < sizeof(info)) {
        return MCUBOOT_RES_ERR_HEADER;
    }
    if (mcuboot_stream_area_read(off, info, sizeof(info)) != 0) {
        return MCUBOOT_RES_ERR_FS_READ;
    }
    uint32_t tlv_tot = get_le16(info + 2);
    if (get_le16(info) != IMAGE_TLV_INFO_MAGIC || tlv_tot < sizeof(info) || tlv_tot > stream_size - off) {
        return MCUBOOT_RES_ERR_HEADER;
    }
    *out = off + tlv_tot;
    return 0;
}

// Header checks that need no more than the first bytes of the file: magic, and sizes that
// neither overflow nor exceed what the target can hold. They run before the file is read
// through.
static int check_header(mcuboot_stream_t *s, uint32_t capacity) {
    uint8_t hdr[IMAGE_HEADER_SIZE];
    uint32_t done = 0;

    while (done < sizeof(hdr)) {
        int r = s->read(s, hdr + done, sizeof(hdr) - done);
        if (r < 0) {
            return -r;
        }
        if (r == 0) {
            return MCUBOOT_RES_ERR_HEADER;
        }
        done += r;
    }
    uint32_t total = get_le16(hdr + 8);
    if (get_le32(hdr) != IMAGE_MAGIC || __builtin_add_overflow(total, get_le32(hdr + 12), &total) ||
        __builtin_add_overflow(total, get_le16(hdr + 10), &total) || __builtin_add_overflow(total, FSLOAD_TLV_INFO_SIZE, &total)) {
        return MCUBOOT_RES_ERR_HEADER;
    }
    if (total > capacity) {
        return MCUBOOT_RES_ERR_TOO_BIG;
    }
    return 0;
}

// ---- pass 1: open and validate ----

static int open_source(const fsload_request_t *rq, mcuboot_stream_t **out) {
    mcuboot_stream_t *s = NULL;
    int r;

    switch (rq->fs_type) {
        #if defined(MCUBOOT_FSLOAD_FAT) && MCUBOOT_FSLOAD_FAT
        case MCUBOOT_FSLOAD_FS_FAT:
            r = mcuboot_vfs_fat_mount(&rq->win[0], rq->path, &s);
            break;
        #endif
        #if defined(MCUBOOT_FSLOAD_LFS2) && MCUBOOT_FSLOAD_LFS2
        case MCUBOOT_FSLOAD_FS_LFS2:
            r = mcuboot_vfs_lfs2_mount(&rq->win[0], rq->block_size, rq->path, &s);
            break;
        #endif
        #if defined(MCUBOOT_FSLOAD_RAW) && MCUBOOT_FSLOAD_RAW
        case MCUBOOT_FSLOAD_FS_RAW:
            r = mcuboot_vfs_raw_mount(&rq->win[0], rq->win[1].len != 0 ? &rq->win[1] : NULL, &s);
            break;
        #endif
        default:
            return MCUBOOT_RES_ERR_FS_MOUNT;
    }
    if (r < 0) {
        return -r;
    }
    r = s->open(s);
    if (r < 0) {
        return -r;
    }
    *out = s;
    return 0;
}

#if defined(MCUBOOT_FSLOAD_GZIP) && MCUBOOT_FSLOAD_GZIP
// True when the file starts with the gzip magic. Leaves the stream at an undefined
// position.
static bool is_gzip(mcuboot_stream_t *s) {
    uint8_t m[3];
    if (s->size < sizeof(m)) {
        return false;
    }
    uint32_t done = 0;
    while (done < sizeof(m)) {
        int r = s->read(s, m + done, sizeof(m) - done);
        if (r <= 0) {
            return false;
        }
        done += r;
    }
    return m[0] == 0x1f && m[1] == 0x8b && m[2] == 0x08;
}
#endif

// Opens the file and primes it as the stream area. On success *top is the stream whose size
// is the file size (decompressed for gzip) and *len the length of the image in it.
static int open_stream(const fsload_request_t *rq, const fsload_target_t *t, mcuboot_stream_t **top, uint32_t *len) {
    mcuboot_stream_t *s;
    bool size_known = true;
    int r = open_source(rq, &s);
    if (r != 0) {
        return r;
    }

    #if defined(MCUBOOT_FSLOAD_GZIP) && MCUBOOT_FSLOAD_GZIP
    if (is_gzip(s)) {
        // The gzip reader enforces the capacity on the decompressed output.
        r = s->rewind(s);
        if (r < 0) {
            return -r;
        }
        mcuboot_stream_t *gz;
        r = mcuboot_gz_attach(s, t->upd.capacity, &gz);
        if (r < 0) {
            return -r;
        }
        r = gz->open(gz);
        if (r < 0) {
            return -r;
        }
        s = gz;
        size_known = false;
    } else
    #endif
    {
        if (s->size > t->upd.capacity) {
            return MCUBOOT_RES_ERR_TOO_BIG;
        }
        r = s->rewind(s);
        if (r < 0) {
            return -r;
        }
    }

    r = check_header(s, t->upd.capacity);
    if (r != 0) {
        return r;
    }
    r = mcuboot_fsload_stream_prime(s, size_known, t->upd.fap->fa_size - t->upd.update_off);
    if (r < 0) {
        return -r;
    }
    r = image_length(s->size, len);
    if (r != 0) {
        return r;
    }
    if (*len > t->upd.capacity) {
        return MCUBOOT_RES_ERR_TOO_BIG;
    }
    *top = s;
    return 0;
}

// ---- pass 2: write ----

#if defined(MCUBOOT_POLICY_SINGLE) && MCUBOOT_POLICY_SINGLE

static int intent_open(const struct flash_area **fap) {
    if (flash_area_open(MCUBOOT_AREA_INTENT, fap) != 0) {
        return MCUBOOT_RES_ERR_LAYOUT;
    }
    return 0;
}

static int intent_write(const uint8_t *elems, size_t len) {
    const struct flash_area *fap;
    int r = intent_open(&fap);
    if (r != 0) {
        return r;
    }
    uint32_t align = flash_area_align(fap);
    uint32_t total = FSLOAD_INTENT_HEADER + len;
    uint32_t padded = (total + align - 1) / align * align;
    if (len > MCUBOOT_REQ_ELEMS_MAX || padded > fap->fa_size || padded > sizeof(fsload_buf)) {
        return MCUBOOT_RES_ERR_TOO_BIG;
    }

    memset(fsload_buf, flash_area_erased_val(fap), padded);
    fsload_buf[0] = FSLOAD_INTENT_MAGIC & 0xff;
    fsload_buf[1] = (FSLOAD_INTENT_MAGIC >> 8) & 0xff;
    fsload_buf[2] = (FSLOAD_INTENT_MAGIC >> 16) & 0xff;
    fsload_buf[3] = FSLOAD_INTENT_MAGIC >> 24;
    fsload_buf[4] = len & 0xff;
    fsload_buf[5] = len >> 8;
    fsload_buf[6] = 0;
    fsload_buf[7] = 0;
    uint32_t crc = mcuboot_crc32(mcuboot_crc32(0, fsload_buf, 8), elems, len);
    fsload_buf[8] = crc & 0xff;
    fsload_buf[9] = (crc >> 8) & 0xff;
    fsload_buf[10] = (crc >> 16) & 0xff;
    fsload_buf[11] = crc >> 24;
    memcpy(fsload_buf + FSLOAD_INTENT_HEADER, elems, len);

    if (flash_area_erase(fap, 0, fap->fa_size) != 0 || flash_area_write(fap, 0, fsload_buf, padded) != 0) {
        return MCUBOOT_RES_ERR_FLASH;
    }
    return 0;
}

// Erases the intent unit when it holds anything.
static int intent_erase(void) {
    const struct flash_area *fap;
    uint8_t first[4];
    if (intent_open(&fap) != 0) {
        return MCUBOOT_RES_ERR_LAYOUT;
    }
    uint8_t erased = flash_area_erased_val(fap);
    if (flash_area_read(fap, 0, first, sizeof(first)) == 0 &&
        first[0] == erased && first[1] == erased && first[2] == erased && first[3] == erased) {
        return 0;
    }
    return flash_area_erase(fap, 0, fap->fa_size) == 0 ? 0 : MCUBOOT_RES_ERR_FLASH;
}

#endif // MCUBOOT_POLICY_SINGLE

bool mcuboot_intent_load(mcuboot_request_t *req) {
    #if defined(MCUBOOT_POLICY_SINGLE) && MCUBOOT_POLICY_SINGLE
    const struct flash_area *fap;
    uint8_t hdr[FSLOAD_INTENT_HEADER];
    if (intent_open(&fap) != 0 || flash_area_read(fap, 0, hdr, sizeof(hdr)) != 0) {
        return false;
    }
    uint32_t len = get_le16(hdr + 4);
    if (get_le32(hdr) != FSLOAD_INTENT_MAGIC || get_le16(hdr + 6) != 0 || len > MCUBOOT_REQ_ELEMS_MAX ||
        len > fap->fa_size - sizeof(hdr)) {
        return false;
    }
    if (len != 0 && flash_area_read(fap, sizeof(hdr), req->elems, len) != 0) {
        return false;
    }
    if (mcuboot_crc32(mcuboot_crc32(0, hdr, 8), req->elems, len) != get_le32(hdr + 8)) {
        return false;
    }
    req->mode = MCUBOOT_REQ_FSLOAD;
    req->elems_len = len;
    return true;
    #else
    (void)req;
    return false;
    #endif
}

#if !(defined(MCUBOOT_POLICY_SINGLE) && MCUBOOT_POLICY_SINGLE)

// Policies swap and overwrite-external: nothing may stay pending from an earlier session, and
// a half-written image must never look complete. This is the session begin of mcuboot_update.h
// (trailer first, then the spare sector).
static int session_begin(const fsload_target_t *t) {
    uint32_t fail_off;
    return mcuboot_update_begin(&t->upd, &fail_off) != 0 ? MCUBOOT_RES_ERR_FLASH : 0;
}

#endif // !MCUBOOT_POLICY_SINGLE

// Copies the first len bytes of the stream to the target, erasing each sector just before
// its first write. The last chunk is padded with the erased value to the write unit. detail
// receives the target offset of a flash failure.
static int copy_stream(const fsload_target_t *t, mcuboot_stream_t *s, uint32_t len, uint32_t *detail) {
    const struct flash_area *fap = t->upd.fap;
    uint32_t erased_to = t->upd.update_off;
    int r = s->rewind(s);
    if (r < 0) {
        return -r;
    }

    for (uint32_t pos = 0; pos < len;) {
        uint32_t n = len - pos;
        if (n > sizeof(fsload_buf)) {
            n = sizeof(fsload_buf);
        }
        uint32_t got = 0;
        while (got < n) {
            r = s->read(s, fsload_buf + got, n - got);
            if (r < 0) {
                return -r;
            }
            if (r == 0) {
                return MCUBOOT_RES_ERR_FS_READ;
            }
            got += r;
        }

        uint32_t off = t->upd.update_off + pos;
        uint32_t padded = (n + t->align - 1) / t->align * t->align;
        memset(fsload_buf + n, t->erased_val, padded - n);
        uint32_t end = off + padded;
        if (end > erased_to) {
            uint32_t new_end = (end + t->erase - 1) / t->erase * t->erase;
            *detail = erased_to;
            if (new_end > t->upd.trailer_off || flash_area_erase(fap, erased_to, new_end - erased_to) != 0) {
                return MCUBOOT_RES_ERR_FLASH;
            }
            erased_to = new_end;
        }
        *detail = off;
        if (flash_area_write(fap, off, fsload_buf, padded) != 0) {
            return MCUBOOT_RES_ERR_FLASH;
        }
        pos += n;
        MCUBOOT_WATCHDOG_FEED();
    }
    return 0;
}

static int install(const fsload_target_t *t, mcuboot_stream_t *s, uint32_t image_len, const uint8_t *elems, size_t elems_len, uint32_t *detail) {
    int r;
    #if defined(MCUBOOT_POLICY_SINGLE) && MCUBOOT_POLICY_SINGLE
    r = intent_write(elems, elems_len);
    #else
    (void)elems;
    (void)elems_len;
    r = session_begin(t);
    #endif
    if (r != 0) {
        return r;
    }
    r = copy_stream(t, s, image_len, detail);
    if (r != 0) {
        return r;
    }
    #if defined(MCUBOOT_POLICY_SINGLE) && MCUBOOT_POLICY_SINGLE
    // The only slot holds what pass 2 read back from the file, which may differ from what pass 1
    // validated. Validate the slot again before dropping the intent; a differing copy keeps the
    // retry record.
    mcuboot_validate_result_t vr = mcuboot_validate_view(t->upd.fap, VALIDATE_FULL | VALIDATE_CHECK_TARGET | VALIDATE_CHECK_DOWNGRADE);
    if (vr.code != MCUBOOT_RES_OK) {
        *detail = vr.detail;
        return vr.code;
    }
    return intent_erase();
    #else
    if (mcuboot_update_mark_pending(false) != 0) {
        return MCUBOOT_RES_ERR_PENDING;
    }
    return 0;
    #endif
}

// ---- run ----

// Runs both passes. *pass2 is set once pass 2 has started writing.
static int execute(const fsload_request_t *rq, const uint8_t *elems, size_t elems_len, mcuboot_validate_result_t *vr, bool *pass2, uint32_t *detail) {
    fsload_target_t t;
    mcuboot_stream_t *s;
    uint32_t image_len;

    mcuboot_fsload_stream_unbind();
    int r = target_open(&t);
    if (r != 0) {
        return r;
    }

    r = open_stream(rq, &t, &s, &image_len);
    if (r != 0) {
        // A stream error carries the reason the validator would otherwise report as a
        // plain flash error.
        *detail = mcuboot_fsload_stream_error() != 0 ? (uint32_t)-mcuboot_fsload_stream_error() : 0;
        return r;
    }

    *vr = mcuboot_validate_view(mcuboot_fsload_stream_area(), VALIDATE_FULL | VALIDATE_CHECK_TARGET | VALIDATE_CHECK_DOWNGRADE);
    *detail = vr->detail;
    if (vr->code != MCUBOOT_RES_OK) {
        int serr = mcuboot_fsload_stream_error();
        if (vr->code == MCUBOOT_RES_ERR_FLASH && serr != 0) {
            return -serr;
        }
        return vr->code;
    }

    #if defined(MCUBOOT_LOG_LEVEL) && MCUBOOT_LOG_LEVEL >= MCUBOOT_LOG_LEVEL_DEBUG
    const mcuboot_fsload_stream_stats_t *st = mcuboot_fsload_stream_stats();
    MCUBOOT_LOG_DBG("validated %u bytes: %u reads, %u cache hits, %u rewinds, %u bytes discarded",
        (unsigned)image_len, (unsigned)st->reads, (unsigned)st->cache_hits, (unsigned)st->rewinds, (unsigned)st->discarded);
    #endif

    *pass2 = true;
    return install(&t, s, image_len, elems, elems_len, detail);
}

// Stores the result word of a STATUS element at addr, a RAM address chosen by the app. The
// default is a plain write; a port that must unlock the register or backup domain the address
// lies in overrides it.
__attribute__((weak)) void mcuboot_fsload_status_store(uint32_t addr, uint32_t value) {
    *(volatile uint32_t *)(uintptr_t)addr = value;
}

// The word named by a STATUS element receives 0 on success or the negated result code. The
// address comes from the application, so it must be word aligned and inside the RAM window the
// board declares (MCUBOOT_STATUS_RAM_START..END), which excludes the bootloader's own RAM, the
// request region and the peripherals. A board without a window stores nothing.
static void store_status(const fsload_request_t *rq, int result) {
    #if defined(MCUBOOT_STATUS_RAM_START)
    if (rq->has_status && (rq->status_addr & 3) == 0 && rq->status_addr >= MCUBOOT_STATUS_RAM_START
        && rq->status_addr <= MCUBOOT_STATUS_RAM_END - 4) {
        mcuboot_fsload_status_store(rq->status_addr, (uint32_t)-result);
    }
    #else
    (void)rq;
    (void)result;
    #endif
}

int mcuboot_fsload_run(const uint8_t *elems, size_t len) {
    // Static: the path alone is 255 bytes and the validator runs below this frame.
    static fsload_request_t rq;
    mcuboot_validate_result_t vr;
    bool pass2 = false;
    uint32_t detail = 0;

    memset(&vr, 0, sizeof(vr));

    int r = parse_request(elems, len, &rq);
    if (r < 0) {
        r = -r;
    } else {
        r = execute(&rq, elems, len, &vr, &pass2, &detail);
    }

    #if defined(MCUBOOT_POLICY_SINGLE) && MCUBOOT_POLICY_SINGLE
    if (r != MCUBOOT_RES_OK && !pass2) {
        // Nothing was written, so a retry record left by an earlier run must not trigger
        // another attempt with the same request.
        intent_erase();
    }
    #endif

    if (r == MCUBOOT_RES_OK) {
        MCUBOOT_LOG_INF("image installed");
    } else {
        MCUBOOT_LOG_ERR("failed with result %d, detail %u", r, (unsigned)detail);
    }
    store_status(&rq, r);
    return r;
}

#endif // MCUBOOT_FSLOAD_ENABLE
