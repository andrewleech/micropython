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

#ifndef MICROPY_INCLUDED_MIMXRT_MBOOT_DEV_H
#define MICROPY_INCLUDED_MIMXRT_MBOOT_DEV_H

// Device 0 is the memory-mapped FlexSPI NOR. Use 4 KiB erase units across the
// configured device; this is conservative for the 4 KiB sector geometry read
// from the ARCH-MIX FCB and avoids relying on the SDK's conflicting block size.
#if defined(CPU_MIMXRT1052DVL6B)
#define MICROPY_HW_MBOOT_DEV0_BASE (0x60000000u)
#elif defined(CPU_MIMXRT1176DVMAA_cm7)
#define MICROPY_HW_MBOOT_DEV0_BASE (0x30000000u)
#else
#error "mboot_dev.h: unsupported MIMXRT Mboot target"
#endif

#define MICROPY_HW_MBOOT_DEV0_NAME "flexspi-nor"
#define MICROPY_HW_MBOOT_DEV0_WRITE (4u)
#define MICROPY_HW_MBOOT_DEV0_ERASED_VAL (0xff)
#define MICROPY_HW_MBOOT_DEV0_MAPPED (1)
#define MICROPY_HW_MBOOT_DEV0_ECC (0)
#define MICROPY_HW_MBOOT_DEV0_ERASE (4096u)
#define MICROPY_HW_MBOOT_DEV0_SIZE MICROPY_HW_FLASH_SIZE
#define MICROPY_HW_MBOOT_BOOT_SIZE (256u * 1024u)
#define MICROPY_HW_MBOOT_VTOR_ALIGN (0x400u)

#if !defined(MICROPY_HW_ENABLE_MBOOT) || MICROPY_HW_ENABLE_MBOOT != 1
#error "mboot_dev.h is only for explicit Mboot builds"
#endif

#endif // MICROPY_INCLUDED_MIMXRT_MBOOT_DEV_H
