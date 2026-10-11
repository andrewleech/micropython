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

// The PYBD_SF6 flash map with swap using scratch and a version check instead of the security
// counter: two slots of three of the 256 KiB sectors and a scratch area of one sector, which fill
// the 256 KiB run. The largest image is two sectors, 512 KiB. The update audit log is in the 32 KiB
// sectors.

#define MICROPY_HW_MBOOT_PRIMARY_ADDR (0x08040000)
#define MICROPY_HW_MBOOT_PRIMARY_SIZE (3 * 0x40000)
#define MICROPY_HW_MBOOT_SECONDARY_ADDR (0x08100000)
#define MICROPY_MBOOT_SWAP_MODE (MICROPY_MBOOT_SWAP_MODE_SCRATCH)
#define MICROPY_HW_MBOOT_SCRATCH_ADDR (0x081C0000)
#define MICROPY_HW_MBOOT_SCRATCH_SIZE (0x40000)
#define MICROPY_HW_MBOOT_LOG_ADDR (0x08010000)
#define MICROPY_MBOOT_ROLLBACK_COUNTER (0)
#define MICROPY_HW_MBOOT_FS_ADDR (0x80000000)
#define MICROPY_HW_MBOOT_FS_SIZE (2 * 1024 * 1024)
