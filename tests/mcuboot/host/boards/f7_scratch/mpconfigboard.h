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
// the 256 KiB run. The largest image is two sectors, 512 KiB. The update log is in the 32 KiB
// sectors.

#define MCUBOOT_PRIMARY_ADDR (0x08040000)
#define MCUBOOT_PRIMARY_SIZE (3 * 0x40000)
#define MCUBOOT_SECONDARY_ADDR (0x08100000)
#define MCUBOOT_SWAP_MODE (MCUBOOT_SWAP_MODE_SEL_SCRATCH)
#define MCUBOOT_SCRATCH_ADDR (0x081C0000)
#define MCUBOOT_SCRATCH_SIZE (0x40000)
#define MCUBOOT_LOG_ADDR (0x08010000)
#define MCUBOOT_ROLLBACK_COUNTER (0)
#define MCUBOOT_FS_ADDR (0x80000000)
#define MCUBOOT_FS_SIZE (2 * 1024 * 1024)
