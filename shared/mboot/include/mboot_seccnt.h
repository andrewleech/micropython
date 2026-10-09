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

#ifndef MICROPY_INCLUDED_SHARED_MBOOT_MBOOT_SECCNT_H
#define MICROPY_INCLUDED_SHARED_MBOOT_MBOOT_SECCNT_H

#include <stdbool.h>
#include <stdint.h>

// Flash emulated security counter (MBOOT_SECCNT_FLASH). The bootutil entry points
// boot_nv_security_counter_*() in security_cnt.c call these.
//
// MBOOT_AREA_SECCNT holds two erase units. Records of max(write unit, 16) bytes:
// 'SCNT', image id, value, CRC-32 over the first 12 bytes, padded with the erased value.
// The value is the largest valid record; records with a bad CRC or an unreadable word are
// skipped. A new record is appended in the unit that holds the largest value; when that unit
// is full the other unit is erased first and the record becomes its first.

#define MBOOT_SECCNT_REC_MAGIC 0x544E4353u    // bytes "SCNT"

// Creates the first record (value 0) when the area is entirely erased. An area that is not
// erased and holds no valid record is damaged or has lost its counter: init fails with -EIO and
// leaves it as it is. The exception is a power cut inside the first record (unit 0 holds only an
// unreadable or partly programmed first slot, unit 1 is erased): the unit is erased and the first
// record is stored again. Returns 0 or a negative errno.
int mboot_seccnt_init(void);

// Current value for the image; 0 when the area is erased or no record carries the image. Returns
// 0 or a negative errno; -EIO when the area is not erased and holds no valid record.
int mboot_seccnt_flash_read(uint32_t image_id, uint32_t *value);

// Raises the stored value; a value that is not above the stored one is a successful no-op.
// Returns 0 or a negative errno; -EIO as for read.
int mboot_seccnt_flash_write(uint32_t image_id, uint32_t value);

// True when a record can be stored: the area is readable and writable and read does not fail.
bool mboot_seccnt_flash_can_update(uint32_t image_id, uint32_t value);

#endif // MICROPY_INCLUDED_SHARED_MBOOT_MBOOT_SECCNT_H
