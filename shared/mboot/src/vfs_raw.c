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

#include "mboot_layout.h"
#include "mboot_fsload.h"

#if defined(MBOOT_FSLOAD_RAW) && MBOOT_FSLOAD_RAW

// Raw file: the file bytes are the contents of one flash window, optionally followed by a
// second window. There is no structure to parse; every read is bounded by the windows.

typedef struct {
    mboot_stream_t base;
    mboot_fsload_window_t seg[2];
} vfs_raw_t;

static vfs_raw_t vfs_raw;

// There is no file to open: open() and rewind() both set the position to the start.
static int vfs_raw_start(mboot_stream_t *s) {
    s->pos = 0;
    return 0;
}

static int vfs_raw_read(mboot_stream_t *s, uint8_t *dst, uint32_t len) {
    vfs_raw_t *v = (vfs_raw_t *)s;
    uint32_t done = 0;
    while (done < len && s->pos < s->size) {
        const mboot_fsload_window_t *w = &v->seg[0];
        uint32_t off = s->pos;
        if (off >= w->len) {
            off -= w->len;
            w = &v->seg[1];
        }
        uint32_t n = w->len - off;
        if (n > len - done) {
            n = len - done;
        }
        int r = mboot_fsload_window_read(w, off, dst + done, n);
        if (r < 0) {
            return r;
        }
        s->pos += n;
        done += n;
    }
    return done;
}

int mboot_vfs_raw_mount(const mboot_fsload_window_t *seg0, const mboot_fsload_window_t *seg1, mboot_stream_t **out) {
    vfs_raw_t *v = &vfs_raw;
    uint32_t size;
    if (seg0 == NULL || seg0->len == 0) {
        return -MBOOT_RES_ERR_FS_MOUNT;
    }
    v->seg[0] = *seg0;
    v->seg[1].len = 0;
    if (seg1 != NULL) {
        v->seg[1] = *seg1;
    }
    if (__builtin_add_overflow(v->seg[0].len, v->seg[1].len, &size)) {
        return -MBOOT_RES_ERR_FS_MOUNT;
    }
    v->base.open = vfs_raw_start;
    v->base.read = vfs_raw_read;
    v->base.rewind = vfs_raw_start;
    v->base.size = size;
    v->base.pos = 0;
    *out = &v->base;
    return 0;
}

#endif // MBOOT_FSLOAD_RAW
