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

#ifndef MBOOT_TESTS_GLUE_FAKE_MBOOT_H
#define MBOOT_TESTS_GLUE_FAKE_MBOOT_H

// fake_mboot.h - in-memory Mboot environment for the dfu_glue.c host tests.
// Device 0 flash is a byte array, and everything the glue calls (update audit log,
// validation, update slot functions, port, TinyUSB task) is a fake that records
// its calls. The layout comes from the board and port headers the build is
// given (mboot_layout.h). The device table and the DFU region and write range
// tables are the real ones (flash_map.c, dfu_regions.c).

#include <setjmp.h>
#include <stddef.h>
#include <stdint.h>

#include "mboot_api.h"
#include "mcuboot_config/mcuboot_config.h"
#include "mboot_validate.h"

// ---- layout ----

// The tests need the security counter and shadow areas, a one sector trailer and
// 16 byte write units, as on the NUCLEO-H563ZI. The slot policy comes from the
// board: swap (using offset), overwrite-external, or single (no secondary slot,
// the update goes to the primary slot).
#if !defined(MBOOT_SECCNT_FLASH) || !defined(MBOOT_ECC_SHADOW) || MBOOT_DEV0_WRITE != 16 \
    || MBOOT_SECONDARY_DEV != 0 || !MBOOT_DFU_ENABLE || MBOOT_TRAILER_SECTORS != 1
#error "the glue tests need device 0 with 16 byte writes, ECC, the counter area, one trailer sector and the DFU front end"
#endif

#define FK_BASE MBOOT_DEV0_BASE
#define FK_ES MBOOT_SLOT_UNIT
#define FK_DEV_SIZE MBOOT_DEV0_SIZE

#define FK_OFF(addr) ((uint32_t)((addr) - FK_BASE))

#define FK_BOOT_OFF 0u
#define FK_BOOT_SIZE MBOOT_BOOT_SIZE
#define FK_PRIMARY_OFF FK_OFF(MBOOT_PRIMARY_ADDR)
#define FK_PRIMARY_SIZE MBOOT_PRIMARY_SIZE
#define FK_LOG_OFF FK_OFF(MBOOT_LOG_ADDR)
#define FK_LOG_SIZE MBOOT_LOG_SIZE
#define FK_SECCNT_OFF FK_OFF(MBOOT_SECCNT_ADDR)
#define FK_SECCNT_SIZE MBOOT_SECCNT_SIZE
#define FK_SHADOW_OFF FK_OFF(MBOOT_SHADOW_ADDR)
#define FK_SHADOW_SIZE MBOOT_SHADOW_SIZE
#define FK_FS_OFF FK_OFF(MBOOT_FS_ADDR)
#define FK_FS_SIZE MBOOT_FS_SIZE

// Write unit of the device.
#define FK_WRITE MBOOT_DEV0_WRITE

#if MBOOT_POLICY_SINGLE
// The only slot is the primary slot; there is no secondary area.
#define FK_SECONDARY_OFF 0u
#define FK_SECONDARY_SIZE 0u
#define FK_SPARE_SIZE 0u
#define FK_IMAGE_OFF FK_PRIMARY_OFF
#else
#define FK_SECONDARY_OFF FK_OFF(MBOOT_SECONDARY_ADDR)
#define FK_SECONDARY_SIZE MBOOT_SECONDARY_SIZE
// Spare sector at the start of the secondary slot (swap using offset).
#define FK_SPARE_SIZE (MBOOT_UPDATE_SPARE)
#define FK_IMAGE_OFF (FK_SECONDARY_OFF + FK_SPARE_SIZE)
#endif

// The trailer is the last sectors of the update slot; the DFU data region ends before it. The
// only slot of the single policy has none.
#if MBOOT_POLICY_SINGLE
#define FK_TRAILER_SIZE 0u
#else
#define FK_TRAILER_SIZE (MBOOT_TRAILER_SECTORS * FK_ES)
#endif

#define FK_IMAGE_SIZE MBOOT_UPDATE_IMAGE_SIZE

// DFU address of the start of the DFU data region (alt 0), and of an area.
#define FK_ADDR(off) (FK_BASE + (off))
#define FK_IMAGE_ADDR FK_ADDR(FK_IMAGE_OFF)

// Erase units in the DFU data region.
#define FK_IMAGE_SECTORS (FK_IMAGE_SIZE / FK_ES)

// ---- device ----

extern uint8_t fk_flash[FK_DEV_SIZE];

// ---- recorded events, in call order ----

typedef enum {
    EV_ERASE,     // a = device offset, b = length
    EV_WRITE,     // a = device offset, b = length
    EV_LOG,       // a = log type, b = result
    EV_VALIDATE,  // a = view fa_id, b = flags
    EV_PENDING,   // a = image index, b = permanent
    EV_DEINIT,
    EV_RESET,
} fk_ev_kind_t;

typedef struct {
    fk_ev_kind_t kind;
    uint32_t a;
    uint32_t b;
} fk_ev_t;

#define FK_EV_MAX 512

extern fk_ev_t fk_events[FK_EV_MAX];
extern size_t fk_event_count;

// Erase the flash, clear events and counters, reset every knob below to its
// default and re-initialise the DFU core and region table. Returns the
// mboot_dfu_recovery_regions_init() result.
int fk_setup(void);

// Number of recorded events of one kind.
size_t fk_count(fk_ev_kind_t kind);

// 32-bit FNV-1a of device bytes [off, off + len).
uint32_t fk_hash(uint32_t off, uint32_t len);

// Hash of everything outside the DFU-writable ranges: the whole device except
// the secondary slot (the primary slot for policy single).
uint32_t fk_hash_protected(void);

// ---- knobs ----

extern mboot_validate_result_t fk_validate_result;   // returned by mboot_validate_view()
extern int fk_pending_rc;                              // returned by mboot_update_mark_pending()
extern uint32_t fk_trailer_sz;                         // trailer size of the update slot (bytes)
extern int64_t fk_fail_erase_off;                      // erase at this device offset fails (-1: none)
extern unsigned fk_nonblank_writes;                    // writes that hit non-erased bytes
extern uint32_t fk_leds;                               // last mboot_port_led() mask

// Region table and write ranges used by the glue. fk_ranges is the
// mboot_write_ranges[] table (same symbol), a writable copy of the one in
// dfu_regions.c so a test can corrupt it. fk_setup() restores the copy and the
// region table pointer. fk_wrange_t has the layout of mboot_wrange_t.
typedef struct {
    uint32_t addr;
    uint32_t size;
    uint8_t dev;
    uint8_t session_only;
} fk_wrange_t;

extern fk_wrange_t fk_ranges[4] __asm__ ("mboot_write_ranges");
extern const mboot_region_t *fk_regions;
extern size_t fk_region_count;

// ---- port and TinyUSB fakes ----

extern uint32_t fk_ticks;          // mboot_port_ticks_ms()
extern uint32_t fk_tick_step;      // added to fk_ticks on each tud_task()
extern unsigned fk_task_calls;
extern jmp_buf fk_reset_jmp;       // mboot_port_reset() longjmps here
extern void (*fk_task_hook)(unsigned call);   // called from tud_task() with the call count

#endif // MBOOT_TESTS_GLUE_FAKE_MBOOT_H
