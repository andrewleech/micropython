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

#ifndef MICROPY_INCLUDED_SHARED_MCUBOOT_MCUBOOT_UPDATE_H
#define MICROPY_INCLUDED_SHARED_MCUBOOT_MCUBOOT_UPDATE_H

// The update slot and what a session has to do to it, in one place for the DFU front end,
// fsload and the application's Writer. The update slot is the secondary slot.

#include <stdbool.h>
#include <stdint.h>

struct flash_area;

typedef struct {
    const struct flash_area *fap;   // the update slot
    uint32_t update_off;            // area offset of the first byte of an update image: the spare
                                    // sector of swap using offset
    uint32_t trailer_off;           // area offset of the first trailer sector
    uint32_t capacity;              // largest update image in bytes
} mcuboot_update_target_t;

// Describes the update slot from the flash map. Returns 0 or -ENODEV when the layout
// does not leave room for an image.
// The trailer is where mcuboot_flash_slot_trailer_off() puts it, which is where bootutil places
// the secondary trailer. This is the one description of the update slot geometry: the DFU view,
// the DFU write ranges, the application writer and the fsload copy are built on it.
int mcuboot_update_target(mcuboot_update_target_t *t);

// Start of a session: clears the state the next boot would act on. The trailer sectors are
// erased last sector first, so the magic at the very end of the slot is gone before anything
// else and nothing is pending after the first erase; then the spare sector, so that a half
// written image never has a header. Every erase goes through flash_area_erase() (shadow words,
// fault injection). Returns 0 or the negative result of the failed erase; *fail_off is then the
// device offset of that erase. A power loss leaves a state that is never pending.
int mcuboot_update_begin(const mcuboot_update_target_t *t, uint32_t *fail_off);

// Bootloader role. Marks the image in the update slot for the next boot as a test image
// (permanent false) or permanent (true), through boot_set_pending_multi(). A primary slot that
// holds no image header leaves nothing to swap with or to revert to: nothing is marked and
// bootutil's bootstrap copies the validated image into the primary slot at the next boot (a
// mark would also make the downgrade check of bootutil compare the update with the erased
// header and reject it). Returns 0 or a bootutil error.
int mcuboot_update_mark_pending(bool permanent);

#endif // MICROPY_INCLUDED_SHARED_MCUBOOT_MCUBOOT_UPDATE_H
