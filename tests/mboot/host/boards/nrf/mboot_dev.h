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

// Device 0: 1 MiB flash at 0, memory mapped, 4 KiB erase unit, 4 byte write unit, no ECC.

#define MBOOT_DEV0_NAME "internal"
#define MBOOT_DEV0_BASE 0x00000000u
#define MBOOT_DEV0_SIZE 0x100000u
#define MBOOT_DEV0_ERASE 0x1000u
#define MBOOT_DEV0_WRITE 4u
#define MBOOT_DEV0_ERASED_VAL 0xff
#define MBOOT_DEV0_MAPPED 1
#define MBOOT_DEV0_ECC 0
#define MBOOT_BOOT_SIZE 0x10000u
#define MBOOT_VTOR_ALIGN 0x200u
