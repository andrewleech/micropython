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

// mkimg: writes FAT and littlefs2 filesystem images using the libraries the bootloader reads
// them with (lib/oofatfs and lib/littlefs), so the images match what MicroPython writes.
//
//   mkimg fat  <out.img> <size_bytes> [--fat 12|16|32] [--cluster-sectors N] [--no-lfn]
//              [--trim] <op> ...
//   mkimg lfs2 <out.img> <size_bytes> <block_size> <op> ...
//
// An <op> is <dest>=<hostfile>, which copies the host file verbatim to the absolute path <dest>
// (missing directories are created), or rm=<dest>, which deletes <dest>. Operations run in the
// order given.
//
// fat: a bare volume (the boot sector is sector 0, no partition table) with one FAT, as written
// by f_mkfs(). --fat selects the FAT type. The type f_mkfs() produces is checked after
// formatting, and mkimg fails if it differs. Without --cluster-sectors the library chooses the
// cluster size; if that does not give the requested type, the largest cluster size that does is
// used. FAT32 needs more than 65525 clusters, which is at least 33 MiB with 512 byte clusters.
// --trim writes the image only up to the last non-zero sector (the boot sector still declares
// the whole volume). --no-lfn restricts names to upper case 8.3, so no long file name entries
// are written; without it, names that do not fit 8.3 get long file name entries. Every
// operation runs on a fresh mount, so files written after deleting others reuse the freed
// clusters and can be fragmented.
//
// lfs2: read_size = prog_size = 32, cache_size = 128, lookahead_size = 32 and block_cycles = -1,
// the same as the reader. block_size is a multiple of 128 and the block count is
// size_bytes / block_size. Erased blocks read as 0xFF.
//
// Build (TOP is the repository root; one command line):
//   gcc -std=gnu99 -O1 -I$TOP -I$TOP/tests/mboot/fuzz/tools -DFFCONF_H=\"mkimg_ffconf.h\"
//   -DLFS2_NO_DEBUG -DLFS2_NO_WARN -DLFS2_NO_ERROR -o mkimg
//   $TOP/tests/mboot/fuzz/tools/mkimg.c $TOP/lib/oofatfs/ff.c $TOP/lib/oofatfs/ffunicode.c
//   $TOP/lib/littlefs/lfs2.c $TOP/lib/littlefs/lfs2_util.c

#include <ctype.h>
#include <errno.h>
#include <stdarg.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "lib/oofatfs/ff.h"
#include "lib/oofatfs/diskio.h"
#include "lib/littlefs/lfs2.h"

#define SECTOR_SIZE (512u)
#define PATH_MAX_LEN (1024u)

static uint8_t *image;
static size_t image_size;

static void die(const char *fmt, ...) __attribute__((noreturn, format(printf, 1, 2)));

static void die(const char *fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    fprintf(stderr, "mkimg: ");
    vfprintf(stderr, fmt, ap);
    fprintf(stderr, "\n");
    va_end(ap);
    exit(1);
}

static uint8_t *read_file(const char *name, size_t *len) {
    FILE *f = fopen(name, "rb");
    if (f == NULL) {
        die("cannot open %s: %s", name, strerror(errno));
    }
    fseek(f, 0, SEEK_END);
    long n = ftell(f);
    fseek(f, 0, SEEK_SET);
    if (n < 0) {
        die("cannot size %s", name);
    }
    uint8_t *buf = malloc(n ? n : 1);
    if (buf == NULL || fread(buf, 1, n, f) != (size_t)n) {
        die("cannot read %s", name);
    }
    fclose(f);
    *len = n;
    return buf;
}

static void write_image(const char *name, size_t len) {
    FILE *f = fopen(name, "wb");
    if (f == NULL || fwrite(image, 1, len, f) != len || fclose(f) != 0) {
        die("cannot write %s", name);
    }
}

// Splits "<dest>=<hostfile>" or "rm=<dest>". Returns true for a delete.
static bool parse_op(char *arg, char **dest, char **host) {
    if (strncmp(arg, "rm=", 3) == 0) {
        *dest = arg + 3;
        *host = NULL;
    } else {
        char *eq = strchr(arg, '=');
        if (eq == NULL) {
            die("bad operation '%s'", arg);
        }
        *eq = '\0';
        *dest = arg;
        *host = eq + 1;
    }
    if ((*dest)[0] != '/' || strlen(*dest) >= PATH_MAX_LEN) {
        die("destination '%s' must be an absolute path", *dest);
    }
    return *host == NULL;
}

// ---- FAT ----

DWORD get_fattime(void) {
    return 0;
}

DRESULT disk_read(void *drv, BYTE *buff, DWORD sector, UINT count) {
    (void)drv;
    if ((uint64_t)(sector + count) * SECTOR_SIZE > image_size) {
        return RES_PARERR;
    }
    memcpy(buff, image + (size_t)sector * SECTOR_SIZE, (size_t)count * SECTOR_SIZE);
    return RES_OK;
}

DRESULT disk_write(void *drv, const BYTE *buff, DWORD sector, UINT count) {
    (void)drv;
    if ((uint64_t)(sector + count) * SECTOR_SIZE > image_size) {
        return RES_PARERR;
    }
    memcpy(image + (size_t)sector * SECTOR_SIZE, buff, (size_t)count * SECTOR_SIZE);
    return RES_OK;
}

DRESULT disk_ioctl(void *drv, BYTE cmd, void *buff) {
    (void)drv;
    switch (cmd) {
        case IOCTL_INIT:
        case IOCTL_STATUS:
            *(DSTATUS *)buff = 0;
            return RES_OK;
        case CTRL_SYNC:
            return RES_OK;
        case GET_SECTOR_COUNT:
            *(DWORD *)buff = image_size / SECTOR_SIZE;
            return RES_OK;
        case GET_BLOCK_SIZE:
            *(DWORD *)buff = 1;
            return RES_OK;
        default:
            return RES_PARERR;
    }
}

static FATFS fatfs;
static BYTE mkfs_work[SECTOR_SIZE];

static const char *fat_type_name(BYTE t) {
    return t == FS_FAT12 ? "FAT12" : t == FS_FAT16 ? "FAT16" : t == FS_FAT32 ? "FAT32" : "?";
}

static void fat_check(FRESULT r, const char *what, const char *path) {
    if (r != FR_OK) {
        die("%s %s failed: FRESULT %d", what, path, (int)r);
    }
}

// Formats with cluster size au (bytes, 0 = library choice). Returns true when the volume has
// the requested type (0 = any).
static bool fat_format(unsigned fat, DWORD au) {
    memset(image, 0, image_size);
    memset(&fatfs, 0, sizeof(fatfs));
    fatfs.drv = &fatfs;
    BYTE opt = FM_SFD | (fat == 32 ? FM_FAT32 : FM_FAT);
    if (f_mkfs(&fatfs, opt, au, mkfs_work, sizeof(mkfs_work)) != FR_OK) {
        return false;
    }
    memset(&fatfs, 0, sizeof(fatfs));
    fatfs.drv = &fatfs;
    if (f_mount(&fatfs) != FR_OK) {
        return false;
    }
    return fat == 0 || fatfs.fs_type == (fat == 12 ? FS_FAT12 : fat == 16 ? FS_FAT16 : FS_FAT32);
}

static void fat_mkdirs(char *path) {
    for (char *p = path + 1; *p; p++) {
        if (*p == '/') {
            *p = '\0';
            FRESULT r = f_mkdir(&fatfs, path);
            *p = '/';
            if (r != FR_OK && r != FR_EXIST) {
                fat_check(r, "mkdir", path);
            }
        }
    }
}

// Upper-cases every component and checks that it is a valid 8.3 name.
static void fat_sfn_only(char *path) {
    char *comp = path + 1;
    for (char *p = path + 1; ; p++) {
        if (*p == '/' || *p == '\0') {
            size_t base = 0, ext = 0;
            bool in_ext = false;
            for (char *c = comp; c < p; c++) {
                if (*c == '.' && !in_ext && c != comp) {
                    in_ext = true;
                } else if (!isalnum((unsigned char)*c) && strchr("_-~!#$%&'()@^`{}", *c) == NULL) {
                    die("--no-lfn: '%s' is not an 8.3 name", path);
                } else if (in_ext) {
                    ext++;
                } else {
                    base++;
                }
                *c = toupper((unsigned char)*c);
            }
            if (base == 0 || base > 8 || ext > 3) {
                die("--no-lfn: '%s' is not an 8.3 name", path);
            }
            if (*p == '\0') {
                break;
            }
            comp = p + 1;
        }
    }
}

static int mkimg_fat(int argc, char **argv) {
    const char *out = argv[0];
    size_t size = strtoull(argv[1], NULL, 0);
    unsigned fat = 0, cluster_sectors = 0;
    bool no_lfn = false, trim = false;
    int i = 2;
    for (; i < argc && strncmp(argv[i], "--", 2) == 0; i++) {
        if (strcmp(argv[i], "--fat") == 0 && i + 1 < argc) {
            fat = atoi(argv[++i]);
            if (fat != 12 && fat != 16 && fat != 32) {
                die("--fat must be 12, 16 or 32");
            }
        } else if (strcmp(argv[i], "--cluster-sectors") == 0 && i + 1 < argc) {
            cluster_sectors = atoi(argv[++i]);
            if (cluster_sectors == 0 || cluster_sectors > 128 || (cluster_sectors & (cluster_sectors - 1))) {
                die("--cluster-sectors must be a power of two up to 128");
            }
        } else if (strcmp(argv[i], "--no-lfn") == 0) {
            no_lfn = true;
        } else if (strcmp(argv[i], "--trim") == 0) {
            trim = true;
        } else {
            die("unknown option %s", argv[i]);
        }
    }
    if (size < 64 * SECTOR_SIZE || size % SECTOR_SIZE != 0 || size > 0xffffffffu) {
        die("size must be a multiple of 512, at least 32768");
    }
    image_size = size;
    image = malloc(size);
    if (image == NULL) {
        die("out of memory");
    }

    bool ok;
    if (cluster_sectors != 0) {
        ok = fat_format(fat, cluster_sectors * SECTOR_SIZE);
    } else {
        ok = fat_format(fat, 0);
        for (unsigned cs = 128; !ok && cs >= 1; cs >>= 1) {
            ok = fat_format(fat, cs * SECTOR_SIZE);
        }
    }
    if (!ok) {
        die("cannot create a %s volume of %zu bytes", fat ? (fat == 12 ? "FAT12" : fat == 16 ? "FAT16" : "FAT32") : "FAT", size);
    }

    for (; i < argc; i++) {
        char *dest, *host;
        bool is_rm = parse_op(argv[i], &dest, &host);
        // Each operation starts from a fresh mount with the allocation search at the first
        // cluster, so the clusters of deleted files get reused.
        memset(&fatfs, 0, sizeof(fatfs));
        fatfs.drv = &fatfs;
        fat_check(f_mount(&fatfs), "mount", "");
        fatfs.last_clst = 0xFFFFFFFF;
        if (no_lfn) {
            fat_sfn_only(dest);
        }
        if (is_rm) {
            fat_check(f_unlink(&fatfs, dest), "unlink", dest);
            continue;
        }
        size_t len;
        uint8_t *data = read_file(host, &len);
        fat_mkdirs(dest);
        FIL fp;
        fat_check(f_open(&fatfs, &fp, dest, FA_CREATE_ALWAYS | FA_WRITE), "open", dest);
        UINT bw = 0;
        fat_check(f_write(&fp, data, len, &bw), "write", dest);
        if (bw != len) {
            die("short write to %s (volume full?)", dest);
        }
        fat_check(f_close(&fp), "close", dest);
        free(data);
    }

    size_t out_len = image_size;
    if (trim) {
        while (out_len > SECTOR_SIZE) {
            const uint8_t *s = image + out_len - SECTOR_SIZE;
            size_t k = 0;
            while (k < SECTOR_SIZE && s[k] == 0) {
                k++;
            }
            if (k < SECTOR_SIZE) {
                break;
            }
            out_len -= SECTOR_SIZE;
        }
    }
    write_image(out, out_len);
    printf("%s clusters=%u cluster_sectors=%u fat_sectors=%u data_sector=%u bytes=%zu\n",
        fat_type_name(fatfs.fs_type), (unsigned)(fatfs.n_fatent - 2), (unsigned)fatfs.csize,
        (unsigned)fatfs.fsize, (unsigned)fatfs.database, out_len);
    return 0;
}

// ---- littlefs2 ----

#define LFS_READ_SIZE (32)
#define LFS_PROG_SIZE (32)
#define LFS_CACHE_SIZE (128)
#define LFS_LOOKAHEAD_SIZE (32)

static int lfs_dev_read(const struct lfs2_config *c, lfs2_block_t block, lfs2_off_t off, void *buffer, lfs2_size_t size) {
    memcpy(buffer, image + (size_t)block * c->block_size + off, size);
    return 0;
}

static int lfs_dev_prog(const struct lfs2_config *c, lfs2_block_t block, lfs2_off_t off, const void *buffer, lfs2_size_t size) {
    memcpy(image + (size_t)block * c->block_size + off, buffer, size);
    return 0;
}

static int lfs_dev_erase(const struct lfs2_config *c, lfs2_block_t block) {
    memset(image + (size_t)block * c->block_size, 0xff, c->block_size);
    return 0;
}

static int lfs_dev_sync(const struct lfs2_config *c) {
    (void)c;
    return 0;
}

static void lfs_check(int r, const char *what, const char *path) {
    if (r < 0) {
        die("%s %s failed: littlefs error %d", what, path, r);
    }
}

static int mkimg_lfs2(int argc, char **argv) {
    const char *out = argv[0];
    size_t size = strtoull(argv[1], NULL, 0);
    unsigned block_size = strtoul(argv[2], NULL, 0);
    if (block_size < LFS_CACHE_SIZE || block_size % LFS_CACHE_SIZE != 0) {
        die("block_size must be a multiple of %d", LFS_CACHE_SIZE);
    }
    if (size % block_size != 0 || size / block_size < 2 || size > 0xffffffffu) {
        die("size must be a multiple of block_size, at least two blocks");
    }
    image_size = size;
    image = malloc(size);
    if (image == NULL) {
        die("out of memory");
    }
    memset(image, 0xff, size);

    static lfs2_t lfs;
    static struct lfs2_config config;
    memset(&config, 0, sizeof(config));
    config.read = lfs_dev_read;
    config.prog = lfs_dev_prog;
    config.erase = lfs_dev_erase;
    config.sync = lfs_dev_sync;
    config.read_size = LFS_READ_SIZE;
    config.prog_size = LFS_PROG_SIZE;
    config.block_size = block_size;
    config.block_count = size / block_size;
    config.block_cycles = -1;
    config.cache_size = LFS_CACHE_SIZE;
    config.lookahead_size = LFS_LOOKAHEAD_SIZE;
    lfs_check(lfs2_format(&lfs, &config), "format", "");
    lfs_check(lfs2_mount(&lfs, &config), "mount", "");

    for (int i = 3; i < argc; i++) {
        char *dest, *host;
        bool is_rm = parse_op(argv[i], &dest, &host);
        if (is_rm) {
            lfs_check(lfs2_remove(&lfs, dest), "remove", dest);
            continue;
        }
        size_t len;
        uint8_t *data = read_file(host, &len);
        for (char *p = dest + 1; *p; p++) {
            if (*p == '/') {
                *p = '\0';
                int r = lfs2_mkdir(&lfs, dest);
                *p = '/';
                if (r < 0 && r != LFS2_ERR_EXIST) {
                    lfs_check(r, "mkdir", dest);
                }
            }
        }
        lfs2_file_t f;
        lfs_check(lfs2_file_open(&lfs, &f, dest, LFS2_O_WRONLY | LFS2_O_CREAT | LFS2_O_TRUNC), "open", dest);
        lfs2_ssize_t n = lfs2_file_write(&lfs, &f, data, len);
        lfs_check(n, "write", dest);
        if ((size_t)n != len) {
            die("short write to %s", dest);
        }
        lfs_check(lfs2_file_close(&lfs, &f), "close", dest);
        free(data);
    }
    lfs_check(lfs2_unmount(&lfs), "unmount", "");
    write_image(out, image_size);
    printf("lfs2 block_size=%u block_count=%u bytes=%zu\n", block_size, (unsigned)config.block_count, image_size);
    return 0;
}

int main(int argc, char **argv) {
    if (argc >= 4 && strcmp(argv[1], "fat") == 0) {
        return mkimg_fat(argc - 2, argv + 2);
    }
    if (argc >= 5 && strcmp(argv[1], "lfs2") == 0) {
        return mkimg_lfs2(argc - 2, argv + 2);
    }
    fprintf(stderr,
        "usage: mkimg fat <out.img> <size> [--fat 12|16|32] [--cluster-sectors N] [--no-lfn] [--trim] <dest>=<hostfile> ...\n"
        "       mkimg lfs2 <out.img> <size> <block_size> <dest>=<hostfile> ...\n"
        "       (rm=<dest> deletes a path)\n");
    return 2;
}
