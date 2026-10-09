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

#ifndef MICROPY_INCLUDED_SHARED_MBOOT_MBOOT_FSLOAD_H
#define MICROPY_INCLUDED_SHARED_MBOOT_MBOOT_FSLOAD_H

// fsload: install a signed MCUboot image from a file on a filesystem (or a raw flash
// window) of the device. Bootloader role only.
//
// The request is an element stream (shared/mboot/include/mboot_elem.h) with a MOUNT
// element, a FSLOAD element and optionally a STATUS element. fsload runs in two passes:
//   pass 1  open the file as a stream, wrap it in a read-only struct flash_area and run
//           mboot_validate_view() over it; nothing is written;
//   pass 2  erase and write the target slot from the stream, then mark the image pending.
//
// Everything read from the device, the element stream and the file is untrusted until pass
// 1 has validated the image, so the readers are bounded: reads stay inside the mounted window,
// arithmetic on sizes is checked, there is no recursion and no heap. The gzip decoder is lib/uzlib;
// the glue limits its output to the capacity of the target.

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "mboot_types.h"
#include "mboot_request.h"

// ---- entry points ----

// Runs the request in elems[0..len). If the request carries a STATUS element, stores 0 on
// success or the negated result code on failure in the word it names. Returns MBOOT_RES_OK
// or the mboot_result_t of the failure. On success the new image is pending (policies swap
// and overwrite-external) or in place (policy single); the caller resets.
int mboot_fsload_run(const uint8_t *elems, size_t len);

// Policy single only. Loads the retry request written by pass 2 from MBOOT_AREA_INTENT into
// req (mode MBOOT_REQ_FSLOAD). Returns false when the area holds no record with a valid CRC.
bool mboot_intent_load(mboot_request_t *req);

// Writes the result word of a STATUS element. Weak: the default stores value at addr; a port
// whose status address is in a register that needs unlocking provides its own.
void mboot_fsload_status_store(uint32_t addr, uint32_t value);

// Maximum stack in bytes that mboot_fsload_run() and its readers need on a Cortex-M33, from
// -fstack-usage and -fcallgraph-info of gcc 14.3 at -Os. The deepest chain is the priming read of
// a gzip file on littlefs2 (1208 bytes) plus an allowance for the flash read below
// mboot_flash_dev_read() and the flash write functions, counted as 64 byte leaves. Not included:
// mboot_validate_view() and bootutil, which run below the same entry point.
#ifndef MBOOT_FSLOAD_STACK_MAX
#define MBOOT_FSLOAD_STACK_MAX 1280
#endif

// ---- element stream constants (payloads as in ports/stm32/mboot/fsload.c) ----

#define MBOOT_FSLOAD_FS_FAT   1u
#define MBOOT_FSLOAD_FS_LFS1  2u  // not offered
#define MBOOT_FSLOAD_FS_LFS2  3u
#define MBOOT_FSLOAD_FS_RAW   4u

// MOUNT payload lengths for 32-bit addresses: mount_point, fs_type, base, len, [arg2, [arg3]].
#define MBOOT_FSLOAD_MOUNT_LEN_MIN  10u
#define MBOOT_FSLOAD_MOUNT_LEN_ARG2 14u
#define MBOOT_FSLOAD_MOUNT_LEN_ARG3 18u

// FSLOAD payload: mount_point followed by a path of 1..MBOOT_FSLOAD_PATH_MAX bytes.
#define MBOOT_FSLOAD_PATH_MAX 254u

// STATUS payload length: a 32-bit RAM address.
#define MBOOT_FSLOAD_STATUS_LEN 4u

// Block size used for littlefs2 mounts that give none.
#define MBOOT_FSLOAD_LFS_DEFAULT_BLOCK_SIZE 4096u

// ---- block device window ----

// A byte range of one declared flash device. Every read made by a filesystem reader goes
// through mboot_fsload_window_read(), which refuses anything outside the window.
typedef struct {
    uint8_t dev;        // index into mboot_devs[]
    uint32_t base;      // device offset of the first byte
    uint32_t len;       // bytes
} mboot_fsload_window_t;

// Returns 0, or -MBOOT_RES_ERR_FS_READ when [off, off + len) is not inside the window or
// the device read fails.
int mboot_fsload_window_read(const mboot_fsload_window_t *w, uint32_t off, void *dst, uint32_t len);

// Maps the DFU-space range [addr, addr + len) onto one declared device. Returns 0 and fills w,
// or -MBOOT_RES_ERR_REQUEST when len is 0, the range wraps or it is not inside one device.
int mboot_fsload_window_from_addr(mboot_fsload_window_t *w, uint32_t addr, uint32_t len);

// ---- streams ----

typedef struct mboot_stream mboot_stream_t;
struct mboot_stream {
    int (*open)(mboot_stream_t *s);                               // positions at byte 0 of the (decompressed) file
    int (*read)(mboot_stream_t *s, uint8_t *dst, uint32_t len);   // sequential; returns bytes read, 0 at the end, or <0
    int (*rewind)(mboot_stream_t *s);                             // back to byte 0
    uint32_t size;                                                  // decompressed size
    uint32_t pos;
};

// open() and rewind() return 0 or a negative mboot_result_t; read() returns the byte count,
// 0 at the end of the file, or a negative mboot_result_t.

// ---- readers ----
//
// Each reader keeps one static context. mount() checks that the window holds the filesystem
// and returns the stream of the file; the stream's open() then opens the file and sets size.
// The path must stay valid until the stream is no longer used. mount() returns 0 or
// -MBOOT_RES_ERR_FS_MOUNT; open() of a missing file fails with -MBOOT_RES_ERR_FS_OPEN.
//
// Library build flags: lib/oofatfs needs -DFFCONF_H=\"mboot_ffconf.h\"; lib/littlefs needs
// -DLFS2_NO_MALLOC -DLFS2_NO_DEBUG -DLFS2_NO_WARN -DLFS2_NO_ERROR -DLFS2_NO_ASSERT
// -DLFS2_READONLY; lib/uzlib needs -DUZLIB_CONF_PARANOID_CHECKS=1.

// FAT (lib/oofatfs), read only.
int mboot_vfs_fat_mount(const mboot_fsload_window_t *w, const char *path, mboot_stream_t **out);

// littlefs2 (lib/littlefs), read only. block_size 0 selects
// MBOOT_FSLOAD_LFS_DEFAULT_BLOCK_SIZE.
int mboot_vfs_lfs2_mount(const mboot_fsload_window_t *w, uint32_t block_size, const char *path, mboot_stream_t **out);

// Raw: the file is the bytes of seg0 followed by the bytes of seg1 (may be NULL).
int mboot_vfs_raw_mount(const mboot_fsload_window_t *seg0, const mboot_fsload_window_t *seg1, mboot_stream_t **out);

// gzip over src. The stream decompresses src, which must be open;
// limit is the largest decompressed size accepted. An input that decompresses to more fails
// with -MBOOT_RES_ERR_FS_GZIP, as do a corrupt stream, a CRC-32 or ISIZE mismatch and a
// truncated stream. The size of the stream is not known before it has been read to the end
// (mboot_fsload_stream_prime() does that). There is one gzip context; its window is 32 KiB
// and is cleared on every (re)start so the output is a function of the input only.
int mboot_gz_attach(mboot_stream_t *src, uint32_t limit, mboot_stream_t **out);

// ---- stream as flash area ----

typedef struct {
    uint32_t reads;         // calls of mboot_stream_area_read()
    uint32_t cache_hits;    // of which served from the head or tail window
    uint32_t rewinds;       // stream rewinds caused by backward reads
    uint32_t discarded;     // bytes read and thrown away to reach a read offset
} mboot_fsload_stream_stats_t;

// Reads s from start to end, which fixes s->size (size_known: s->size is already the size
// and must match), keeps the head and tail windows for backward reads and binds s as the
// stream area, area_size bytes large (the size of the slot the image is validated for; the bytes
// after the end of the file read as erased flash). Returns 0 or the negative result of the stream;
// a file larger than area_size is -MBOOT_RES_ERR_TOO_BIG.
int mboot_fsload_stream_prime(mboot_stream_t *s, bool size_known, uint32_t area_size);

// Releases the stream area. Called at the start of every run.
void mboot_fsload_stream_unbind(void);

struct flash_area;

// The read-only area of the primed stream (fa_id MBOOT_AREA_ID_STREAM, fa_device_id
// MBOOT_DEV_STREAM, fa_off 0, fa_size the area_size given to the prime call), or NULL when none is bound.
// mboot_stream_area_read() (flash_map_backend.h) reads it.
const struct flash_area *mboot_fsload_stream_area(void);

// Negative result of the last failed read of the underlying stream, or 0. The validator
// only sees a flash error; this tells a file system or gzip failure apart.
int mboot_fsload_stream_error(void);

const mboot_fsload_stream_stats_t *mboot_fsload_stream_stats(void);

#endif // MICROPY_INCLUDED_SHARED_MBOOT_MBOOT_FSLOAD_H
