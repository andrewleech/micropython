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

#include "mcuboot_layout.h"
#include "mcuboot_fsload.h"

#if defined(MCUBOOT_FSLOAD_LFS2) && MCUBOOT_FSLOAD_LFS2

#include "lib/littlefs/lfs2.h"

// Read-only littlefs2 reader for fsload.
//
// lib/littlefs is built with LFS2_READONLY, LFS2_NO_MALLOC and the assertions off, so the
// configuration is validated here. The block size is a multiple of the 128 byte cache. The
// block count is derived from the MOUNT window (block_count * block_size <= window length, at
// least two blocks) and must equal the one in the superblock. name_max and file_max stay at the
// library defaults, which are also the largest values lfs2_mount() accepts from the superblock.
// A file larger than the filesystem is refused when it is opened. Cache, read and lookahead
// buffers are static. Every read is bounded to the window, and every operation has a budget of
// bytes read so a metadata loop the library doesn't detect ends in an I/O error. The block
// count is not left to the superblock (cfg block_count 0) because lfs2_mount() then divides by
// it.

#define LFS_READ_SIZE (32)
#define LFS_PROG_SIZE (32)
#define LFS_CACHE_SIZE (4 * LFS_READ_SIZE)
#define LFS_LOOKAHEAD_SIZE (32)

// An operation may read the window this many times over, plus a constant.
#define LFS_BUDGET_PASSES (4u)
#define LFS_BUDGET_EXTRA (65536u)

typedef struct {
    mcuboot_stream_t base;
    mcuboot_fsload_window_t win;
    struct lfs2_config config;
    lfs2_t lfs;
    struct lfs2_file_config filecfg;
    lfs2_file_t file;
    const char *path;
    uint32_t budget;        // bytes left to read in the current operation
    uint8_t filebuf[LFS_CACHE_SIZE];
    uint8_t read_buffer[LFS_CACHE_SIZE];
    uint8_t prog_buffer[LFS_CACHE_SIZE];
    uint8_t lookahead_buffer[LFS_LOOKAHEAD_SIZE];
} vfs_lfs2_t;

static vfs_lfs2_t vfs_lfs2;

static void vfs_lfs2_set_budget(vfs_lfs2_t *v) {
    uint32_t b;
    if (__builtin_mul_overflow(v->win.len, LFS_BUDGET_PASSES, &b) || __builtin_add_overflow(b, LFS_BUDGET_EXTRA, &b)) {
        b = UINT32_MAX;
    }
    v->budget = b;
}

static int dev_read(const struct lfs2_config *c, lfs2_block_t block, lfs2_off_t off, void *buffer, lfs2_size_t size) {
    vfs_lfs2_t *v = c->context;
    uint32_t addr;
    if (block >= c->block_count || off > c->block_size || size > c->block_size - off) {
        return LFS2_ERR_IO;
    }
    if (size > v->budget) {
        v->budget = 0;
        return LFS2_ERR_IO;
    }
    v->budget -= size;
    // block < block_count and block_count * block_size <= window length: no overflow.
    addr = block * c->block_size + off;
    if (mcuboot_fsload_window_read(&v->win, addr, buffer, size) < 0) {
        return LFS2_ERR_IO;
    }
    return LFS2_ERR_OK;
}

static int dev_prog(const struct lfs2_config *c, lfs2_block_t block, lfs2_off_t off, const void *buffer, lfs2_size_t size) {
    (void)c;
    (void)block;
    (void)off;
    (void)buffer;
    (void)size;
    return LFS2_ERR_IO;
}

static int dev_erase(const struct lfs2_config *c, lfs2_block_t block) {
    (void)c;
    (void)block;
    return LFS2_ERR_IO;
}

static int dev_sync(const struct lfs2_config *c) {
    (void)c;
    return LFS2_ERR_OK;
}

static int vfs_lfs2_open(mcuboot_stream_t *s) {
    vfs_lfs2_t *v = (vfs_lfs2_t *)s;
    vfs_lfs2_set_budget(v);
    memset(&v->file, 0, sizeof(v->file));
    memset(&v->filecfg, 0, sizeof(v->filecfg));
    v->filecfg.buffer = v->filebuf;
    if (lfs2_file_opencfg(&v->lfs, &v->file, v->path, LFS2_O_RDONLY, &v->filecfg) < 0) {
        return -MCUBOOT_RES_ERR_FS_OPEN;
    }
    lfs2_soff_t size = lfs2_file_size(&v->lfs, &v->file);
    if (size < 0 || (uint64_t)size > (uint64_t)v->config.block_count * v->config.block_size) {
        lfs2_file_close(&v->lfs, &v->file);
        return -MCUBOOT_RES_ERR_FS_OPEN;
    }
    s->size = size;
    s->pos = 0;
    return 0;
}

static int vfs_lfs2_rewind(mcuboot_stream_t *s) {
    vfs_lfs2_t *v = (vfs_lfs2_t *)s;
    vfs_lfs2_set_budget(v);
    if (lfs2_file_rewind(&v->lfs, &v->file) < 0) {
        return -MCUBOOT_RES_ERR_FS_READ;
    }
    s->pos = 0;
    return 0;
}

static int vfs_lfs2_read(mcuboot_stream_t *s, uint8_t *dst, uint32_t len) {
    vfs_lfs2_t *v = (vfs_lfs2_t *)s;
    if (len > INT32_MAX) {
        len = INT32_MAX;
    }
    vfs_lfs2_set_budget(v);
    lfs2_ssize_t n = lfs2_file_read(&v->lfs, &v->file, dst, len);
    if (n < 0 || (uint32_t)n > len) {
        return -MCUBOOT_RES_ERR_FS_READ;
    }
    s->pos += n;
    return n;
}

int mcuboot_vfs_lfs2_mount(const mcuboot_fsload_window_t *w, uint32_t block_size, const char *path, mcuboot_stream_t **out) {
    vfs_lfs2_t *v = &vfs_lfs2;
    if (block_size == 0) {
        block_size = MCUBOOT_FSLOAD_LFS_DEFAULT_BLOCK_SIZE;
    }
    // littlefs needs at least the two blocks of the root metadata pair, and a block size
    // that is a multiple of its cache.
    if (block_size < LFS_CACHE_SIZE || block_size % LFS_CACHE_SIZE != 0 || w->len / block_size < 2) {
        return -MCUBOOT_RES_ERR_FS_MOUNT;
    }

    v->win = *w;
    v->path = path;
    struct lfs2_config *config = &v->config;
    memset(config, 0, sizeof(*config));
    config->context = v;
    config->read = dev_read;
    config->prog = dev_prog;
    config->erase = dev_erase;
    config->sync = dev_sync;
    config->read_size = LFS_READ_SIZE;
    config->prog_size = LFS_PROG_SIZE;
    config->block_size = block_size;
    config->block_count = w->len / block_size;
    config->block_cycles = -1;
    config->cache_size = LFS_CACHE_SIZE;
    config->lookahead_size = LFS_LOOKAHEAD_SIZE;
    config->read_buffer = v->read_buffer;
    config->prog_buffer = v->prog_buffer;
    config->lookahead_buffer = v->lookahead_buffer;

    vfs_lfs2_set_budget(v);
    if (lfs2_mount(&v->lfs, config) < 0) {
        return -MCUBOOT_RES_ERR_FS_MOUNT;
    }
    v->base.open = vfs_lfs2_open;
    v->base.read = vfs_lfs2_read;
    v->base.rewind = vfs_lfs2_rewind;
    v->base.size = 0;
    v->base.pos = 0;
    *out = &v->base;
    return 0;
}

#endif // MCUBOOT_FSLOAD_LFS2
