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

#ifndef MICROPY_INCLUDED_TESTS_MCUBOOT_FUZZ_CONFIG_MCUBOOT_LAYOUT_H
#define MICROPY_INCLUDED_TESTS_MCUBOOT_FUZZ_CONFIG_MCUBOOT_LAYOUT_H

// Replaces shared/mcuboot/include/mcuboot_layout.h for the fsload host tests and fuzz targets. The
// geometry comes from tests/mcuboot/fuzz/harness/host_env.h rather than a board, every fsload
// option is on, and validation is tinycrypt ECDSA P-256 so test_fsload can run the real bootutil.
// The update policy is chosen on the command line: FUZZ_POLICY_SINGLE, or swap using offset by
// default.

#define MCUBOOT_ROLE_BOOTLOADER 1

#define MCUBOOT_SIGN_EC256 1
#define MCUBOOT_USE_TINYCRYPT 1
#define MCUBOOT_VALIDATE_PRIMARY_SLOT 1
#define MCUBOOT_USE_FLASH_AREA_GET_SECTORS 1
#define MCUBOOT_MAX_IMG_SECTORS 128
#define MCUBOOT_IMAGE_NUMBER 1
#define MCUBOOT_BOOT_MAX_ALIGN 16
#define MCUBOOT_BOOT_TMPBUF_SZ 256
#define MCUBOOT_HEADER_SIZE 0x400
#define MCUBOOT_HAVE_ASSERT_H 1
#define MCUBOOT_HAVE_LOGGING 1
#ifndef MCUBOOT_LOG_LEVEL
#define MCUBOOT_LOG_LEVEL 0
#endif

// Slots of tests/mcuboot/fuzz/harness/host_env.h: 128 KiB primary of 4 KiB sectors. The largest
// image is the slot minus the trailer sectors (two for swap using offset with 128 sector tables
// and 16 byte write units), or for single the slot minus its 80 byte trailer reservation. Swap
// using offset takes no image of one sector or less.
// The policy macros are always defined, to 0 or 1.
#define MCUBOOT_PRIMARY_SIZE 0x20000
#define MCUBOOT_POLICY_OVERWRITE_EXTERNAL 0
#if defined(FUZZ_POLICY_SINGLE)
#define MCUBOOT_SINGLE_APPLICATION_SLOT 1
#define MCUBOOT_POLICY_SWAP 0
#define MCUBOOT_POLICY_SINGLE 1
#define MCUBOOT_MAX_IMAGE_SIZE 0x1FFB0
#define MCUBOOT_MIN_IMAGE_SIZE 0
#define MCUBOOT_TRAILER_SECTORS 1
#else
#define MCUBOOT_SWAP_USING_OFFSET 1
#define MCUBOOT_POLICY_SWAP 1
#define MCUBOOT_POLICY_SINGLE 0
#define MCUBOOT_MAX_IMAGE_SIZE 0x1E000
#define MCUBOOT_MIN_IMAGE_SIZE 0x1001
#define MCUBOOT_TRAILER_SECTORS 2
#endif

// The RAM an application may name in the STATUS element of a request.
#define MCUBOOT_STATUS_RAM_START 0x20000000u
#define MCUBOOT_STATUS_RAM_END 0x20010000u

#define MCUBOOT_FSLOAD_ENABLE 1
#define MCUBOOT_FSLOAD_RAW 1
#define MCUBOOT_FSLOAD_FAT 1
#define MCUBOOT_FSLOAD_LFS2 1
#define MCUBOOT_FSLOAD_GZIP 1

#endif // MICROPY_INCLUDED_TESTS_MCUBOOT_FUZZ_CONFIG_MCUBOOT_LAYOUT_H
