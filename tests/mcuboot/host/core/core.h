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

#ifndef MICROPY_INCLUDED_TESTS_MCUBOOT_HOST_CORE_CORE_H
#define MICROPY_INCLUDED_TESTS_MCUBOOT_HOST_CORE_CORE_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

// Shared declarations of the core glue host harness (core_port.c, core_unit.c, core_test.c).

// Exit codes of a simulated boot attempt (a forked child; the fake flash and this block live in
// shared memory and survive the child like flash and retained RAM survive a reset).
#define CORE_EXIT_RESET (88)      // mcuboot_port_reset()
#define CORE_EXIT_RECOVERY (89)   // mcuboot_fault_recover(): the bootloader entered recovery

// Result of one boot attempt, written by the child before it exits.
typedef enum {
    CORE_OUT_NONE = 0,
    CORE_OUT_BOOTED,          // boot_go() returned an image
    CORE_OUT_NO_IMAGE,        // boot_go() failed
    CORE_OUT_RECOVERY,        // mcuboot_fault_recover(): assert, map check or scrub failure
    CORE_OUT_DONE,            // an action (set pending, confirm, install) finished
} core_out_kind_t;

typedef struct {
    core_out_kind_t kind;
    int rc;                 // return code of the action
    uint32_t ver_major, ver_minor, ver_rev;
    uint32_t image_off;     // br_image_off
    int swap_type;          // boot_swap_type_multi(0) after boot_go()
    uint32_t seccnt;        // security counter value after the attempt (counter boards)
    char where[96];         // assert location or failure reason
} core_outcome_t;

typedef struct {
    uint8_t req_ram[1024] __attribute__((aligned(8)));
    uint32_t retention;
    uint32_t wdt_feeds;
    uint32_t log_lines;
    uint32_t log_warnings;
    char log_tail[1024];    // most recent log text
    size_t log_tail_len;
    bool log_echo;          // copy log text to stdout
    core_outcome_t out;
} core_shared_t;

extern core_shared_t *core_shared;

void core_port_init(void);

// Fresh fake flash described by the generated device table. The device models ECC when the
// glue has the ECC policy built in or core_force_ecc is set (the control run without the policy).
extern bool core_force_ecc;
void core_flash_fresh(void);

// Unit tests of the glue pieces; return the number of failed checks.
int core_unit_run(void);

#define CORE_CHECK(cond) core_check((cond), #cond, __FILE__, __LINE__)
bool core_check(bool cond, const char *expr, const char *file, int line);
extern int core_failures;

#endif // MICROPY_INCLUDED_TESTS_MCUBOOT_HOST_CORE_CORE_H
