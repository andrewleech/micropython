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

#if defined(MCUBOOT_FSLOAD_FAT) && MCUBOOT_FSLOAD_FAT

#include "lib/oofatfs/ff.h"
#include "lib/oofatfs/diskio.h"

// Read-only FAT reader for fsload over lib/oofatfs.
//
// oofatfs is built with FF_FS_READONLY, FF_FS_TINY and the static LFN buffer variant
// (FF_USE_LFN 1); see mcuboot_ffconf.h. The block device is the MOUNT window and nothing
// else: disk_read() refuses any sector outside it. The directory and cluster walks don't rely
// on a well-formed filesystem to terminate. Every operation (mount, open, rewind, read) has a
// budget of sector reads, and a walk that exhausts it fails with a disk error. The budget of
// an open is the sectors of 4096 directory entries, plus the FAT sectors a fragmented directory
// needs, per path component. A file larger than the volume's clusters can hold is refused,
// which bounds reads along a cluster chain that loops.

#if FF_MAX_SS == FF_MIN_SS
#define SECSIZE (FF_MIN_SS)
#else
#error Unsupported
#endif

#define FAT_DIR_ENTRIES_MAX     (4096u)
#define FAT_DIR_ENTRY_SIZE      (32u)
#define FAT_DIR_SECTORS         (FAT_DIR_ENTRIES_MAX * FAT_DIR_ENTRY_SIZE / SECSIZE)
// Directory sectors plus the FAT sector reads of a directory that is not contiguous.
#define FAT_OPEN_SECTORS        (2 * FAT_DIR_SECTORS + 8)
#define FAT_MOUNT_SECTORS       (16u)
#define FAT_COMPONENTS_MAX      (128u)

typedef struct {
    mcuboot_stream_t base;
    mcuboot_fsload_window_t win;
    uint32_t num_blocks;
    uint32_t budget;        // sector reads left for the current operation
    const char *path;
    FATFS fatfs;
    FIL fp;
} vfs_fat_t;

static vfs_fat_t vfs_fat;

DRESULT disk_read(void *pdrv, BYTE *buf, DWORD sector, UINT count) {
    vfs_fat_t *v = pdrv;
    if (count == 0 || sector >= v->num_blocks || count > v->num_blocks - sector) {
        return RES_PARERR;
    }
    if (count > v->budget) {
        v->budget = 0;
        return RES_ERROR;
    }
    v->budget -= count;
    // sector < num_blocks <= window length / SECSIZE, so the product fits in 32 bits.
    if (mcuboot_fsload_window_read(&v->win, sector * SECSIZE, buf, count * SECSIZE) < 0) {
        return RES_ERROR;
    }
    return RES_OK;
}

DRESULT disk_ioctl(void *pdrv, BYTE cmd, void *buf) {
    vfs_fat_t *v = pdrv;
    switch (cmd) {
        case CTRL_SYNC:
            return RES_OK;

        case GET_SECTOR_COUNT:
            *((DWORD *)buf) = v->num_blocks;
            return RES_OK;

        case GET_SECTOR_SIZE:
            *((WORD *)buf) = SECSIZE;
            return RES_OK;

        case GET_BLOCK_SIZE:
            *((DWORD *)buf) = 1; // erase block size in units of sector size
            return RES_OK;

        case IOCTL_INIT:
        case IOCTL_STATUS:
            *((DSTATUS *)buf) = STA_PROTECT;
            return RES_OK;

        default:
            return RES_PARERR;
    }
}

static unsigned vfs_fat_path_components(const char *path) {
    unsigned n = 0;
    bool in_component = false;
    for (; *path != '\0'; ++path) {
        if (*path == '/') {
            in_component = false;
        } else if (!in_component) {
            in_component = true;
            ++n;
        }
    }
    if (n == 0) {
        n = 1;
    }
    return n > FAT_COMPONENTS_MAX ? FAT_COMPONENTS_MAX : n;
}

static int vfs_fat_open(mcuboot_stream_t *s) {
    vfs_fat_t *v = (vfs_fat_t *)s;
    v->budget = vfs_fat_path_components(v->path) * FAT_OPEN_SECTORS;
    if (f_open(&v->fatfs, &v->fp, v->path, FA_READ) != FR_OK) {
        return -MCUBOOT_RES_ERR_FS_OPEN;
    }
    uint64_t capacity = (uint64_t)(v->fatfs.n_fatent - 2) * v->fatfs.csize * SECSIZE;
    if (v->fp.obj.objsize > capacity) {
        return -MCUBOOT_RES_ERR_FS_OPEN;
    }
    s->size = v->fp.obj.objsize;
    s->pos = 0;
    return 0;
}

static int vfs_fat_rewind(mcuboot_stream_t *s) {
    vfs_fat_t *v = (vfs_fat_t *)s;
    v->budget = FAT_MOUNT_SECTORS;
    if (f_lseek(&v->fp, 0) != FR_OK) {
        return -MCUBOOT_RES_ERR_FS_READ;
    }
    s->pos = 0;
    return 0;
}

static int vfs_fat_read(mcuboot_stream_t *s, uint8_t *dst, uint32_t len) {
    vfs_fat_t *v = (vfs_fat_t *)s;
    UINT n;
    if (len > INT32_MAX) {
        len = INT32_MAX;
    }
    // Whole sectors read straight into dst plus one FAT sector per cluster boundary.
    v->budget = 2 * (len / SECSIZE) + 8;
    if (f_read(&v->fp, dst, len, &n) != FR_OK || n > len) {
        return -MCUBOOT_RES_ERR_FS_READ;
    }
    s->pos += n;
    return n;
}

int mcuboot_vfs_fat_mount(const mcuboot_fsload_window_t *w, const char *path, mcuboot_stream_t **out) {
    vfs_fat_t *v = &vfs_fat;
    memset(&v->fatfs, 0, sizeof(v->fatfs));
    memset(&v->fp, 0, sizeof(v->fp));
    v->win = *w;
    v->num_blocks = w->len / SECSIZE;
    v->path = path;
    v->budget = FAT_MOUNT_SECTORS;
    if (v->num_blocks == 0) {
        return -MCUBOOT_RES_ERR_FS_MOUNT;
    }
    v->fatfs.drv = v;
    if (f_mount(&v->fatfs) != FR_OK) {
        return -MCUBOOT_RES_ERR_FS_MOUNT;
    }
    v->base.open = vfs_fat_open;
    v->base.read = vfs_fat_read;
    v->base.rewind = vfs_fat_rewind;
    v->base.size = 0;
    v->base.pos = 0;
    *out = &v->base;
    return 0;
}

#endif // MCUBOOT_FSLOAD_FAT
