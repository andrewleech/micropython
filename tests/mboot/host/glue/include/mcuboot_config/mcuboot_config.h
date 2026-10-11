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

#ifndef MICROPY_INCLUDED_TESTS_MBOOT_HOST_GLUE_MBOOT_CONFIG_H
#define MICROPY_INCLUDED_TESTS_MBOOT_HOST_GLUE_MBOOT_CONFIG_H

// Minimal bootutil configuration for the host sweep: swap using offset,
// ECDSA P-256 with tinycrypt, primary slot validated on every boot. The
// numeric values come from the layout through the Makefile.

#ifndef HOST_MAX_ALIGN
#error "HOST_MAX_ALIGN (layout max_align) must be defined"
#endif
#ifndef HOST_MAX_IMG_SECTORS
#error "HOST_MAX_IMG_SECTORS (layout max_img_sectors) must be defined"
#endif

#define MCUBOOT_SWAP_USING_OFFSET 1
#define MCUBOOT_SIGN_EC256 1
#define MCUBOOT_USE_TINYCRYPT 1
#define MCUBOOT_VALIDATE_PRIMARY_SLOT 1

#if HOST_MAX_ALIGN > 8
#define MCUBOOT_BOOT_MAX_ALIGN HOST_MAX_ALIGN
#endif

#define MCUBOOT_USE_FLASH_AREA_GET_SECTORS 1
#define MCUBOOT_MAX_IMG_SECTORS HOST_MAX_IMG_SECTORS
#define MCUBOOT_IMAGE_NUMBER 1

#define MCUBOOT_HAVE_LOGGING 1
#ifndef MCUBOOT_LOG_LEVEL
#define MCUBOOT_LOG_LEVEL MCUBOOT_LOG_LEVEL_INFO
#endif
#define MCUBOOT_HAVE_ASSERT_H 1
#define MCUBOOT_WATCHDOG_FEED() do { } while (0)
#define MCUBOOT_CPU_IDLE() do { } while (0)

#endif // MICROPY_INCLUDED_TESTS_MBOOT_HOST_GLUE_MBOOT_CONFIG_H
