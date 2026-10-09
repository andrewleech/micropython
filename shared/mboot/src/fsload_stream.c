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

#include "mboot_layout.h"
#include "mboot_fsload.h"
#include "mboot_port.h"
#include "flash_map_backend/flash_map_backend.h"
#include "sysflash/sysflash.h"

#if defined(MBOOT_FSLOAD_ENABLE) && MBOOT_FSLOAD_ENABLE

// Presents a sequential stream (mboot_stream_t) as a read-only struct flash_area, so that
// bootutil_img_validate() can run over a file that is not in flash.
//
// The area is as large as the slot the image is validated for, not as the file. With the
// single slot and overwrite-external policies bootutil bounds the image by the size of the
// area it is given less the trailer (bootutil_max_image_size(), boot_status_off() and
// boot_swap_info_off()), so an area of exactly the file size would always be refused. Bytes of
// the area after the end of the file read as erased flash.
//
// bootutil reads the header, then the body and protected TLVs in chunks, then the TLV area
// forward, and afterwards goes back to the header and the start of the TLV area (security
// counter, key lookup, version checks). A stream can't seek backwards, so a backward read
// rewinds and discards up to the target offset; for a gzip stream that means decompressing
// everything before it again. To avoid that, mboot_fsload_stream_prime() reads the stream
// once from start to end, which also fixes the decompressed size, and keeps the first
// FSLOAD_HEAD_CACHE bytes and the last FSLOAD_TAIL_CACHE bytes. Reads that fall wholly inside
// either window come from RAM; everything else is read from the stream.

#ifndef MBOOT_FSLOAD_HEAD_CACHE
#define MBOOT_FSLOAD_HEAD_CACHE 64u
#endif
#ifndef MBOOT_FSLOAD_TAIL_CACHE
#define MBOOT_FSLOAD_TAIL_CACHE 1024u
#endif

#define PRIME_CHUNK 256u

#define ERASED 0xffu

static struct {
    mboot_stream_t *s;
    struct flash_area area;
    int err;                                    // negative result of the last failed stream call, or 0
    uint32_t head_len;                          // valid bytes in head
    uint32_t tail_len;                          // valid bytes in tail; tail holds [size - tail_len, size)
    uint8_t head[MBOOT_FSLOAD_HEAD_CACHE ? MBOOT_FSLOAD_HEAD_CACHE : 1];
    uint8_t tail[MBOOT_FSLOAD_TAIL_CACHE ? MBOOT_FSLOAD_TAIL_CACHE : 1];
    mboot_fsload_stream_stats_t stats;
} fs;

static int stream_fail(int r) {
    fs.err = r;
    return r;
}

// Moves the stream to byte off: continues forward, or rewinds first when off is behind the
// current position. Bytes before off are read into scratch (len > 0 bytes long) and
// discarded.
static int stream_seek(uint32_t off, uint8_t *scratch, uint32_t scratch_len) {
    mboot_stream_t *s = fs.s;
    if (off < s->pos) {
        int r = s->rewind(s);
        if (r < 0) {
            return stream_fail(r);
        }
        s->pos = 0;
        fs.stats.rewinds++;
    }
    while (s->pos < off) {
        uint32_t n = off - s->pos;
        if (n > scratch_len) {
            n = scratch_len;
        }
        int r = s->read(s, scratch, n);
        if (r < 0) {
            return stream_fail(r);
        }
        if (r == 0) {
            return stream_fail(-MBOOT_RES_ERR_FS_READ);
        }
        fs.stats.discarded += r;
    }
    return 0;
}

int mboot_stream_area_read(uint32_t off, void *dst_in, uint32_t len) {
    uint8_t *dst = dst_in;
    mboot_stream_t *s = fs.s;
    if (s == NULL) {
        return -EINVAL;
    }
    if (off > fs.area.fa_size || len > fs.area.fa_size - off) {
        return -ERANGE;
    }
    if (len == 0) {
        return 0;
    }
    fs.stats.reads++;

    // Bytes at and after the end of the file are erased flash.
    uint32_t pad = 0;
    if (off >= s->size) {
        memset(dst, ERASED, len);
        return 0;
    }
    if (len > s->size - off) {
        pad = len - (s->size - off);
        len -= pad;
        memset(dst + len, ERASED, pad);
    }

    if (off + len <= fs.head_len) {
        memcpy(dst, fs.head + off, len);
        fs.stats.cache_hits++;
        return 0;
    }
    if (fs.tail_len != 0 && off >= s->size - fs.tail_len) {
        memcpy(dst, fs.tail + (off - (s->size - fs.tail_len)), len);
        fs.stats.cache_hits++;
        return 0;
    }

    if (stream_seek(off, dst, len) < 0) {
        return -EIO;
    }
    uint32_t done = 0;
    while (done < len) {
        int r = s->read(s, dst + done, len - done);
        if (r < 0) {
            stream_fail(r);
            return -EIO;
        }
        if (r == 0) {
            stream_fail(-MBOOT_RES_ERR_FS_READ);
            return -EIO;
        }
        done += r;
    }
    return 0;
}

static void reverse_bytes(uint8_t *p, uint32_t n) {
    for (uint32_t i = 0; i < n / 2; ++i) {
        uint8_t t = p[i];
        p[i] = p[n - 1 - i];
        p[n - 1 - i] = t;
    }
}

// Rotates p[0..n) left by k places (k < n) without a second buffer.
static void rotate_left(uint8_t *p, uint32_t n, uint32_t k) {
    reverse_bytes(p, k);
    reverse_bytes(p + k, n - k);
    reverse_bytes(p, n);
}

int mboot_fsload_stream_prime(mboot_stream_t *s, bool size_known, uint32_t area_size) {
    uint8_t chunk[PRIME_CHUNK];
    uint32_t count = 0;
    uint32_t ring_pos = 0;

    fs.s = NULL;
    fs.err = 0;
    fs.head_len = 0;
    fs.tail_len = 0;
    memset(&fs.stats, 0, sizeof(fs.stats));

    int r = s->rewind(s);
    if (r < 0) {
        return r;
    }
    s->pos = 0;
    for (;;) {
        r = s->read(s, chunk, sizeof(chunk));
        if (r < 0) {
            return r;
        }
        if (r == 0) {
            break;
        }
        uint32_t n = r;
        if (n > sizeof(chunk) || __builtin_add_overflow(count, n, &count)) {
            return -MBOOT_RES_ERR_FS_READ;
        }

        if (fs.head_len < MBOOT_FSLOAD_HEAD_CACHE) {
            uint32_t h = MBOOT_FSLOAD_HEAD_CACHE - fs.head_len;
            if (h > n) {
                h = n;
            }
            memcpy(fs.head + fs.head_len, chunk, h);
            fs.head_len += h;
        }

        // The tail window is a ring over the end of the stream; it is rotated into stream
        // order once the end is known.
        if (MBOOT_FSLOAD_TAIL_CACHE != 0) {
            const uint8_t *src = chunk;
            uint32_t m = n;
            if (m > MBOOT_FSLOAD_TAIL_CACHE) {
                src += m - MBOOT_FSLOAD_TAIL_CACHE;
                m = MBOOT_FSLOAD_TAIL_CACHE;
            }
            uint32_t first = MBOOT_FSLOAD_TAIL_CACHE - ring_pos;
            if (first > m) {
                first = m;
            }
            memcpy(fs.tail + ring_pos, src, first);
            memcpy(fs.tail, src + first, m - first);
            ring_pos = (ring_pos + m) % MBOOT_FSLOAD_TAIL_CACHE;
            fs.tail_len = fs.tail_len + m > MBOOT_FSLOAD_TAIL_CACHE ? MBOOT_FSLOAD_TAIL_CACHE : fs.tail_len + m;
        }
        mboot_port_wdt_feed();
    }

    if (size_known && count != s->size) {
        return -MBOOT_RES_ERR_FS_READ;
    }
    if (MBOOT_FSLOAD_TAIL_CACHE != 0 && fs.tail_len == MBOOT_FSLOAD_TAIL_CACHE) {
        // The oldest byte is at ring_pos: rotate the ring so that it is at index 0.
        rotate_left(fs.tail, MBOOT_FSLOAD_TAIL_CACHE, ring_pos);
    }
    if (count > area_size) {
        return -MBOOT_RES_ERR_TOO_BIG;
    }
    s->size = count;
    s->pos = count;
    fs.s = s;
    fs.area.fa_id = MBOOT_AREA_ID_STREAM;
    fs.area.fa_device_id = MBOOT_DEV_STREAM;
    fs.area.pad16 = 0;
    fs.area.fa_off = 0;
    fs.area.fa_size = area_size;
    return 0;
}

void mboot_fsload_stream_unbind(void) {
    fs.s = NULL;
    fs.err = 0;
}

const struct flash_area *mboot_fsload_stream_area(void) {
    return fs.s != NULL ? &fs.area : NULL;
}

int mboot_fsload_stream_error(void) {
    return fs.err;
}

const mboot_fsload_stream_stats_t *mboot_fsload_stream_stats(void) {
    return &fs.stats;
}

#endif // MBOOT_FSLOAD_ENABLE
