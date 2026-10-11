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

#ifndef MICROPY_INCLUDED_TESTS_MBOOT_HOST_BOOTLOADER_BL_H
#define MICROPY_INCLUDED_TESTS_MBOOT_HOST_BOOTLOADER_BL_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "mboot_dfu_recovery.h"
#include "mboot_types.h"

// Shared declarations of the main flow host harness (bl_port.c, bl_host.c, bl_test.c).
//
// Every boot attempt is a forked child that runs the real mboot_main() (or an action of the
// application). The fake flash (tests/mboot/host/fake_flash.c) and the block below live in
// shared memory and survive the child like flash and retained RAM survive a reset; everything
// else is lost, as at a reset. The child ends through one of the port functions: the jump into
// the application, a reset, or the DFU front end, which the harness plays the host side of.

// Exit status of a child.
#define BL_EXIT_JUMP (90)       // mboot_port_jump()
#define BL_EXIT_RESET (91)      // mboot_port_reset()
#define BL_EXIT_DFU (92)        // the DFU script ended with the device waiting in DFU
#define BL_EXIT_DONE (93)       // an action of the application finished

typedef enum {
    BL_OUT_NONE = 0,
    BL_OUT_JUMP,
    BL_OUT_RESET,
    BL_OUT_DFU_WAIT,
    BL_OUT_DONE,
} bl_out_kind_t;

// What the DFU front end did in a child, filled by the harness side of the DFU session.
typedef struct {
    bool entered;                   // mboot_dfu_recovery_run() was called
    int why;                        // mboot_recovery_cause_t
    mboot_image_info_t primary;   // primary slot header when the front end started
    bool ran;                       // the install script ran
    unsigned blocks_sent;
    uint8_t block_status;           // DFU status of the last block (0 = OK)
    uint8_t manifest_status;        // DFU status of the manifest step, 0xFF if it did not run
    mboot_dfu_recovery_result_t result;    // vendor request 0x81 after the manifest
} bl_dfu_out_t;

typedef struct {
    bl_out_kind_t kind;
    uint32_t jump_addr;
    int swap_type;                  // boot_swap_type_multi(0) at the jump
    mboot_image_info_t running;   // primary slot header at the jump
    int rc;                         // result of an application action
    bl_dfu_out_t dfu;
} bl_out_t;

typedef enum {
    BL_SCRIPT_NONE = 0,             // DFU is entered and nothing happens
    BL_SCRIPT_INSTALL,              // download blob, then manifest
} bl_script_kind_t;

typedef struct {
    bl_script_kind_t kind;
    const uint8_t *blob;
    size_t len;
    unsigned max_blocks;            // stop (unplug) after this many blocks, 0 = send everything
    bool omit_manifest;             // download only
} bl_script_t;

typedef struct {
    uint8_t req_ram[1024] __attribute__((aligned(8)));
    uint32_t retention;
    uint32_t reset_cause;
    bool entry_forced;
    uint32_t fault_resets;
    uint32_t wdt_feeds;
    uint32_t led;
    uint32_t log_lines;
    bool log_echo;
    char log_tail[2048];
    size_t log_tail_len;
    bl_script_t script;
    bl_out_t out;
} bl_shared_t;

extern bl_shared_t *bl_shared;

void bl_port_init(void);

// Called by the jump and reset stubs and by the harness side of the DFU front end.
void bl_record_jump(uint32_t vt_addr);

// Runs the install script of bl_shared->script against the DFU core (bl_host.c). Does not return.
void bl_dfu_session(void);

#endif // MICROPY_INCLUDED_TESTS_MBOOT_HOST_BOOTLOADER_BL_H
