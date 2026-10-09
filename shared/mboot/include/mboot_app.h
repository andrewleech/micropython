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

#ifndef MICROPY_INCLUDED_SHARED_MBOOT_MBOOT_APP_H
#define MICROPY_INCLUDED_SHARED_MBOOT_MBOOT_APP_H

// Application side API of the MCUboot glue, implemented in shared/mboot/src/app_api.c
// and used by port code. Only plain C types appear here so the users do not need the bootutil or
// flash map headers.
//
// Functions returning int return 0 on success and a negative errno value on failure.

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define MBOOT_APP_SLOT_PRIMARY    0
#define MBOOT_APP_SLOT_SECONDARY  1

// Values of BOOT_SWAP_TYPE_* in bootutil_public.h (checked by app_api.c).
#define MBOOT_APP_SWAP_NONE       1
#define MBOOT_APP_SWAP_TEST       2
#define MBOOT_APP_SWAP_PERM       3
#define MBOOT_APP_SWAP_REVERT     4
#define MBOOT_APP_SWAP_FAIL       5

// Largest element stream accepted by mboot_app_request_fsload() (equals MBOOT_REQ_ELEMS_MAX).
#define MBOOT_APP_ELEMS_MAX       988

typedef struct {
    uint8_t major;
    uint8_t minor;
    uint16_t revision;
    uint32_t build;
} mboot_app_version_t;

typedef struct {
    int swap;                       // MBOOT_APP_SWAP_*, what the next reset will do
    bool confirmed;                 // the primary slot image is confirmed
    bool pending;                   // the next reset swaps or reverts
    bool secondary_valid_header;    // an image header is at the update base of the secondary slot
    bool layout_mismatch;           // the bootloader info block reports a different layout id
} mboot_app_state_t;

typedef struct {
    char version[25];               // NUL terminated
    uint32_t layout_id;
    uint32_t api;
} mboot_app_bl_info_t;

typedef struct {
    uint32_t seq;
    uint8_t type;                   // mboot_log_type_t
    uint8_t result;                 // mboot_result_t
    uint8_t source;                 // mboot_log_source_t
    mboot_app_version_t version;
    uint32_t detail;
} mboot_app_log_entry_t;

typedef struct {
    const char *name;               // static string
    uint32_t start;                 // CPU/DFU address
    uint32_t size;
} mboot_app_slot_t;

// Update slot writer. The caller provides the storage and treats the content as opaque.
#define MBOOT_APP_WRITER_CHUNK    256

typedef struct {
    uint8_t state;
    bool permanent;
    uint8_t write_unit;
    uint8_t erased_val;
    uint32_t erase_unit;
    uint32_t update_off;            // offset of the update base inside the secondary area
    uint32_t capacity;              // bytes that can be accepted
    uint32_t trailer_off;           // offset inside the secondary area of the pre-erased trailer sectors
    uint32_t erased_to;             // update relative offset up to which sectors are erased
    uint32_t flushed;               // update relative offset of the next flash write
    uint32_t total;                 // bytes accepted from the caller
    uint32_t fill;                  // bytes held in chunk
    uint32_t chunk[MBOOT_APP_WRITER_CHUNK / 4];
} mboot_app_writer_t;

// ---- queries ----

// Slot 0 reads the header at the primary slot start, slot 1 at the update base of the secondary
// slot. -ENOENT when there is no valid image header, -EINVAL for an unknown slot.
int mboot_app_version(unsigned slot, mboot_app_version_t *version);

// -ENOENT when the bootloader info block is absent or invalid.
int mboot_app_bootloader_info(mboot_app_bl_info_t *info);

int mboot_app_state(mboot_app_state_t *state);

size_t mboot_app_slot_count(void);
int mboot_app_slot_get(size_t index, mboot_app_slot_t *slot);

// Newest-first: n = 0 is the newest record. -ENOENT past the end.
int mboot_app_log_get(uint32_t n, mboot_app_log_entry_t *entry);

// ---- actions ----

// Confirms the running image. Idempotent. Writes an APP_CONFIRMED log record when the state changed.
int mboot_app_confirm(void);

// Confirms the running image if it is a test image that has not been confirmed yet. Port main()
// calls this after boot.py when MBOOT_CONFIRM_AUTO is set.
int mboot_app_confirm_if_pending(void);

// Marks the image in the secondary slot as pending. -EINVAL when it has no image header, -ENODEV
// on a layout mismatch. Does not reset.
int mboot_app_request_upgrade(bool permanent);

// Reset into normal boot with the request region and retention word cleared.
void mboot_app_reset(void) __attribute__((noreturn));

// Reset into the bootloader DFU mode. Logs APP_DFU_REQUESTED first.
void mboot_app_request_dfu(void) __attribute__((noreturn));

// Reset into the bootloader with a fsload request. Returns -EINVAL if the stream is malformed.
int mboot_app_request_fsload(const uint8_t *elems, size_t len);

// Base (DFU address) and length of the application filesystem area. -ENOENT if the map has none.
int mboot_app_fs_area(uint32_t *base, uint32_t *len);

// ---- Writer ----

// -EPERM for policy single, -ENODEV for a layout/map mismatch, -EIO on flash errors. Erases the
// secondary trailer sectors and the spare sector before returning.
int mboot_app_writer_open(mboot_app_writer_t *w, bool permanent);
// -ENOSPC when the data does not fit (nothing is written then), -EIO on flash errors, -EBADF if closed.
int mboot_app_writer_write(mboot_app_writer_t *w, const uint8_t *buf, size_t len);
// Flushes, checks the image header, marks the image pending. Returns the byte count in *total.
// -EINVAL when no image header is at the update base, or when the image is at most one erase unit
// with swap using offset (MBOOT_MIN_IMAGE_SIZE): the writer has then erased what it wrote.
// The writer is closed after either error.
int mboot_app_writer_finish(mboot_app_writer_t *w, uint32_t *total);
// Erases the first sector of the update slot. Idempotent after abort.
int mboot_app_writer_abort(mboot_app_writer_t *w);

#endif // MICROPY_INCLUDED_SHARED_MBOOT_MBOOT_APP_H
