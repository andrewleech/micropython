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

#ifndef MICROPY_INCLUDED_SHARED_MBOOT_MBOOT_DFU_H
#define MICROPY_INCLUDED_SHARED_MBOOT_MBOOT_DFU_H

// DFU front end of the bootloader (shared/mboot/src/dfu_glue.c). Binds the shared/mboot/dfu
// DFU core to the flash map and to the bootloader's validation and status code.

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "mboot_port.h"

struct flash_area;

// Why the bootloader entered recovery (DFU).
typedef enum {
    REC_NONE = 0,           // not in recovery
    REC_APP_REQUEST,        // the application asked for DFU (request struct or retention word)
    REC_FORCED,             // hardware entry (button, boot pin)
    REC_NO_IMAGE,           // boot_go() found no bootable image; never times out
    REC_FSLOAD_FAILED,      // fsload ran and failed; never times out
    REC_FAULT,              // fault, NMI or assert path; never times out
} mboot_recovery_cause_t;

// One range of flash the DFU front end may erase or program. addr is a DFU/CPU
// address (device base + device offset), size is in bytes and both are multiples of the
// device erase unit; dev indexes mboot_devs[]. session_only marks a range that only the
// session begin may erase (the spare sector and the trailer of the update slot): the DFU
// flash shims treat it as not writable. The table is in dfu_regions.c.
typedef struct {
    uint32_t addr;
    uint32_t size;
    uint8_t dev;
    uint8_t session_only;
} mboot_wrange_t;

// Terminated by an entry whose size is 0. Every flash write and erase issued by the
// DFU core goes through the shims in dfu_glue.c, which refuse anything that is not
// entirely inside one of these ranges (the session_only ones excepted). Independent of the
// DFU region table.
extern const mboot_wrange_t mboot_write_ranges[];

// Phase field of mboot_dfu_recovery_result_t.
#define MBOOT_DFU_PHASE_NONE      0u
#define MBOOT_DFU_PHASE_BEGIN     1u  // session begin: erase of the spare sector and trailer
#define MBOOT_DFU_PHASE_VALIDATE  2u  // validation of the downloaded image
#define MBOOT_DFU_PHASE_PENDING   3u  // marking the image for the swap

// Reply of vendor request 0x81 (MBOOT_VREQ_RESULT), little endian on the wire:
// the outcome of the last session begin or manifest of this recovery session.
// seq counts recorded outcomes from 1; 0 means none yet. code is mboot_result_t,
// source is the update audit log source (SRC_DFU) and detail is the bootutil return code or
// the byte offset of a flash error.
typedef struct {
    uint32_t seq;
    uint16_t code;
    uint8_t source;
    uint8_t phase;
    uint32_t detail;
    uint32_t reserved;
} mboot_dfu_recovery_result_t;

#define MBOOT_DFU_RESULT_WIRE_LEN 16u
_Static_assert(sizeof(mboot_dfu_recovery_result_t) == MBOOT_DFU_RESULT_WIRE_LEN,
    "mboot_dfu_recovery_result_t is the 16-byte wire format");

// Validate the write ranges and region table against the device table
// (devices, erase units, write unit versus MBOOT_DFU_WRITE_ALIGN, alignment) and run
// mboot_region_init(). Returns 0, or a negative errno-style value when the
// tables are inconsistent.
int mboot_dfu_recovery_regions_init(void);

// The area a DFU download lands in, as an argument for mboot_validate_view(): the
// secondary slot without its spare sector. The view uses area id MBOOT_AREA_ID_VIEW; the
// pointer stays valid for the life of the bootloader.
const struct flash_area *mboot_dfu_recovery_target_view(void);

// Run the DFU session loop. Initialises the DFU core and USB and does not return: it
// leaves by mboot_port_reset() when the host finished a download and reset the bus,
// or, for REC_APP_REQUEST and REC_FORCED only, after MBOOT_DFU_TIMEOUT_S seconds without
// DFU activity (0 disables the timeout). A table inconsistency is reported
// through the log and LED and ends in a reset.
MBOOT_NORETURN void mboot_dfu_recovery_run(mboot_recovery_cause_t why);

#endif // MICROPY_INCLUDED_SHARED_MBOOT_MBOOT_DFU_H
