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

// The configuration that the PYBD_SF6 ships with, as a host test model: the single slot policy, the
// bootloader in the first two 32 KiB sectors, the update log in the next two, the intent area of
// fsload in the 128 KiB sector, the primary slot in five of the 256 KiB sectors, the security
// counter in the last two, and a FAT filesystem on the SPI flash for fsload.

#define MCUBOOT_POLICY (MCUBOOT_POLICY_SEL_SINGLE)
#define MCUBOOT_PRIMARY_ADDR (0x08040000)
#define MCUBOOT_PRIMARY_SIZE (5 * 0x40000)
#define MCUBOOT_LOG_ADDR (0x08010000)
#define MCUBOOT_INTENT_ADDR (0x08020000)
#define MCUBOOT_SECCNT_ADDR (0x08180000)
#define MCUBOOT_ROLLBACK_COUNTER (1)
#define MCUBOOT_FSLOAD_FAT (1)
#define MCUBOOT_FS_ADDR (0x80000000)
#define MCUBOOT_FS_SIZE (2 * 1024 * 1024)
