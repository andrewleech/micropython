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

// The NUCLEO-H563ZI layout with a 4 MiB SPI NOR flash (device 1, 4 KiB erase blocks, two per 8 KiB
// erase unit) that holds the secondary slot and, after it, the application filesystem.

#include "../../../../../ports/stm32/boards/NUCLEO_H563ZI/mpconfigboard.h"

#undef MCUBOOT_SECONDARY_ADDR
#define MCUBOOT_SECONDARY_ADDR (0x90000000)
#define MCUBOOT_FS_ADDR (0x90100000)
#define MCUBOOT_FS_SIZE (2 * 1024 * 1024)

#define MBOOT_SPIFLASH_ADDR (0x90000000)
#define MBOOT_SPIFLASH_BYTE_SIZE (4 * 1024 * 1024)
#define MBOOT_SPIFLASH_ERASE_BLOCKS_PER_PAGE (2)

// The readers for a raw flash window and gzip compressed files, on top of FAT.
#define MCUBOOT_FSLOAD_RAW (1)
#define MCUBOOT_FSLOAD_GZIP (1)
