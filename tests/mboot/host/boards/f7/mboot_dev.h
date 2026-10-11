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

// The flash map of the PYBD_SF6 (STM32F767IIK) as a host test model: device 0 is the 2 MiB internal
// flash at 0x08000000 in three runs of erase units (four sectors of 32 KiB, one of 128 KiB, seven
// of 256 KiB), memory mapped, programmed in 4 byte words without ECC. Device 1 is the 2 MiB SPI
// flash #1 at its DFU address 0x80000000, 4 KiB erase blocks, not memory mapped.

#define MBOOT_DEV0_NAME "internal"
#define MBOOT_DEV0_BASE 0x08000000u
#define MBOOT_DEV0_SIZE 0x200000u
#define MBOOT_DEV0_RUNS 3
#define MBOOT_DEV0_RUN0_SIZE (4 * 0x8000u)
#define MBOOT_DEV0_RUN0_ERASE 0x8000u
#define MBOOT_DEV0_RUN1_SIZE (1 * 0x20000u)
#define MBOOT_DEV0_RUN1_ERASE 0x20000u
#define MBOOT_DEV0_RUN2_SIZE (7 * 0x40000u)
#define MBOOT_DEV0_RUN2_ERASE 0x40000u
#define MBOOT_DEV0_WRITE 4u
#define MBOOT_DEV0_ERASED_VAL 0xff
#define MBOOT_DEV0_MAPPED 1
#define MBOOT_DEV0_ECC 0
#define MBOOT_BOOT_SIZE 0x10000u
#define MBOOT_VTOR_ALIGN 0x400u

#define MBOOT_DEV1_NAME "spi"
#define MBOOT_DEV1_BASE (0x80000000)
#define MBOOT_DEV1_SIZE (2 * 1024 * 1024)
#define MBOOT_DEV1_ERASE (4096u)
#define MBOOT_DEV1_WRITE MBOOT_DEV0_WRITE
#define MBOOT_DEV1_ERASED_VAL 0xff
#define MBOOT_DEV1_MAPPED 0
#define MBOOT_DEV1_ECC 0
