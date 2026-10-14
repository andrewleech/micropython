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
//
// Boundary rule. These calls are the only way application code reaches bootloader state, and a
// build may serve them from a separate security domain (TrustZone non-secure callable entry
// points). Every call therefore follows these rules:
// - arguments are scalars, or caller buffers with an explicit length (a fixed size struct
//   declared here counts as one);
// - results are copied out into caller buffers, never returned as pointers;
// - no callbacks;
// - no pointers into bootloader memory are taken or handed out, and no state is kept in caller
//   memory between calls.

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

// Bits of mboot_app_bl_info_t.flags (equal to the info block flags, checked by app_api.c).
#define MBOOT_APP_BL_F_DFU              (1u << 0)   // DFU front end
#define MBOOT_APP_BL_F_FSLOAD           (1u << 1)   // fsload front end
#define MBOOT_APP_BL_F_GZIP             (1u << 2)   // fsload accepts gzip files
#define MBOOT_APP_BL_F_KEY_HW           (1u << 5)   // key hash from hardware (reserved, 0)
#define MBOOT_APP_BL_F_TRUSTZONE        (1u << 6)   // secure world bootloader (reserved, 0)
#define MBOOT_APP_BL_F_ENC_IMAGES       (1u << 7)   // encrypted images (reserved, 0)

// Values of mboot_app_bl_info_t.bl_update.
#define MBOOT_APP_BL_UPDATE_NONE        0
#define MBOOT_APP_BL_UPDATE_IN_PLACE    1
#define MBOOT_APP_BL_UPDATE_DUAL_COPY   2

typedef struct {
    char version[25];               // NUL terminated
    uint32_t layout_id;
    uint32_t api;
    uint32_t flags;                 // MBOOT_APP_BL_F_*; bits 3 and 4 hold bl_update
    uint8_t bl_update;              // MBOOT_APP_BL_UPDATE_*, how the bootloader can be updated
    uint32_t bl_image_id;           // security counter image id of the bootloader
    uint32_t bl_counter;            // security counter value of the running bootloader
    uint8_t key_hash[32];           // SHA-256 of the public key the bootloader trusts
} mboot_app_bl_info_t;


typedef struct {
    char name[12];                  // NUL terminated
    uint32_t start;                 // CPU/DFU address
    uint32_t size;
} mboot_app_slot_t;


// ---- queries ----

// Slot 0 reads the header at the primary slot start, slot 1 at the update base of the secondary
// slot. -ENOENT when there is no valid image header, -EINVAL for an unknown slot.
int mboot_app_version(unsigned slot, mboot_app_version_t *version);

// -ENOENT when the bootloader info block is absent or invalid.
int mboot_app_bootloader_info(mboot_app_bl_info_t *info);

int mboot_app_state(mboot_app_state_t *state);

size_t mboot_app_slot_count(void);
int mboot_app_slot_get(size_t index, mboot_app_slot_t *slot);


// ---- actions ----

// Confirms the running image. Idempotent.
int mboot_app_confirm(void);

// Confirms the running image if it is a test image that has not been confirmed yet. Port main()
// calls this after boot.py when MICROPY_MBOOT_CONFIRM_AUTO is set.
int mboot_app_confirm_if_pending(void);

// Marks the image in the secondary slot as pending. -EINVAL when it has no image header, -ENODEV
// on a layout mismatch. Does not reset.
int mboot_app_request_upgrade(bool permanent);

// Reset into normal boot with the request region and retention word cleared.
void mboot_app_reset(void) __attribute__((noreturn));

// Reset into the bootloader DFU mode.
void mboot_app_request_dfu(void) __attribute__((noreturn));

// Reset into the bootloader with a fsload request. Returns -EINVAL if the stream is malformed.
int mboot_app_request_fsload(const uint8_t *elems, size_t len);

// Base (DFU address) and length of the application filesystem area. -ENOENT if the map has none.
int mboot_app_fs_area(uint32_t *base, uint32_t *len);

// Reset cause handoff: the bootloader clears the hardware reset flags and keeps them for the
// application. Returns those flags and consumes them, or current (the port's hardware reset
// status register) when there are none. Called once at start.
uint32_t mboot_app_reset_flags(uint32_t current);

// ---- Writer ----

// One writer at a time, its state held by app_api.c. Opening while a writer is open returns
// -EBUSY; a finished, aborted or failed writer may be opened again.

// -EPERM for policy single, -ENODEV for a layout/map mismatch, -EIO on flash errors. Erases the
// secondary trailer sectors and the spare sector before returning.
int mboot_app_writer_open(bool permanent);
// -ENOSPC when the data does not fit (nothing is written then), -EIO on flash errors or after a
// failure, -EBADF if no writer is open.
int mboot_app_writer_write(const uint8_t *buf, size_t len);
// Flushes, checks the image header, marks the image pending. Returns the byte count in *total.
// -EINVAL when no image header is at the update base, or when the image is at most one erase unit
// with swap using offset (MBOOT_MIN_IMAGE_SIZE): the writer has then erased what it wrote.
// The writer is closed after either error.
int mboot_app_writer_finish(uint32_t *total);
// Erases the first sector of the update slot and closes the writer. Idempotent after abort;
// -EBADF after finish or when no writer was opened.
int mboot_app_writer_abort(void);

#endif // MICROPY_INCLUDED_SHARED_MBOOT_MBOOT_APP_H
