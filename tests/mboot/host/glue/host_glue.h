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

#ifndef MICROPY_INCLUDED_TESTS_MBOOT_HOST_GLUE_HOST_GLUE_H
#define MICROPY_INCLUDED_TESTS_MBOOT_HOST_GLUE_HOST_GLUE_H

#include <stdint.h>
#include <stdio.h>

typedef struct {
    uint8_t id;             // FLASH_AREA_*
    uint8_t dev;            // fake_flash device
    uint32_t off;           // device relative
    uint32_t size;
} host_area_t;

// Install the flash map used by flash_area_open(). The table is copied.
void host_map_init(const host_area_t *areas, unsigned n);

// Log sink for bootutil messages; NULL discards them.
void host_log_set_file(FILE *f);

// Called by host_assert_fail() before the process ends; the sweep uses it to
// record the failing location.
typedef void (*host_halt_hook_t)(const char *file, int line);
void host_halt_set_hook(host_halt_hook_t fn);

#endif // MICROPY_INCLUDED_TESTS_MBOOT_HOST_GLUE_HOST_GLUE_H
