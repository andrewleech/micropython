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

#ifndef MICROPY_INCLUDED_STM32_MBOOT_MBOOT_DEV_H
#define MICROPY_INCLUDED_STM32_MBOOT_MBOOT_DEV_H

// The flash devices of an STM32 MCUboot build, in the form shared/mboot/include/mboot_layout.h
// needs. The geometry of device 0 is read from the CMSIS device header and ports/stm32/flash.h,
// and tools/mboot_gen.py reads the resulting integer constant expressions with the C
// preprocessor.
//
// Device 0 is the internal flash. On the STM32H5 it is one erase unit (the sector) over the
// whole flash (two banks of 8 KiB sectors), programmed in 128 bit flash words that each carry an
// ECC. On the STM32F7 it is described by the runs of equal sectors of ports/stm32/flash.h (four
// of 32 KiB, one of 128 KiB and seven of 256 KiB), programmed in 32 bit words without ECC.
//
// Device 1 is a SPI flash described with the same MBOOT_SPIFLASH_* macros as ports/stm32/mboot. It
// is read and written through drivers/memory/spiflash.c at a DFU address (MBOOT_SPIFLASH_ADDR) and
// is not memory mapped. Its erase unit is the page of MBOOT_SPIFLASH_ERASE_BLOCKS_PER_PAGE erase
// blocks of 4 KiB. Its write unit is the same as device 0 so the whole system has one alignment.
// A board whose SPI flash is detected at run time (MBOOT_SPIFLASH_LAYOUT_DYNAMIC_MAX_LEN) states
// the size that the layout uses as MBOOT_DEV1_SIZE in mpconfigboard.h: a size that every chip
// the board can carry has.

#if __has_include("stm32h5xx.h")
#include "stm32h5xx.h"
#elif __has_include("stm32f7xx.h")
#include "stm32f7xx.h"
#else
#error "mboot_dev.h: the MCUboot port of the stm32 port supports STM32H5 and STM32F7 only and this build has no CMSIS header of either"
#endif
#include "../../flash.h"

#define MBOOT_DEV0_NAME "internal"
#define MBOOT_DEV0_WRITE FLASH_WORD_BYTES
#define MBOOT_DEV0_ERASED_VAL 0xff
#define MBOOT_DEV0_MAPPED 1
#define MBOOT_DEV0_ECC FLASH_WORD_HAS_ECC

#if defined(STM32H5)

#define MBOOT_DEV0_BASE FLASH_BASE_NS
#define MBOOT_DEV0_SIZE FLASH_SIZE_DEFAULT
#define MBOOT_DEV0_ERASE FLASH_SECTOR_SIZE

// Eight sectors for the bootloader, of which the last 64 bytes are its info block.
#ifndef MBOOT_BOOT_SIZE
#define MBOOT_BOOT_SIZE (8 * MBOOT_DEV0_ERASE)
#endif
// The application's vector table must be aligned to this: the size of the device's vector table,
// rounded up to a power of two.
#define MBOOT_VTOR_ALIGN 0x400u

#else

#if !defined(FLASH_LAYOUT_RUN2_SECTOR_COUNT)
#error "mboot_dev.h: this STM32F7 has no sector table in ports/stm32/flash.h that the MCUboot port can read (FLASH_LAYOUT_RUNn_*); only the STM32F74x to F77x tables are"
#endif

#define MBOOT_DEV0_BASE FLASH_LAYOUT_RUN0_BASE
#define MBOOT_DEV0_RUNS 3
#define MBOOT_DEV0_RUN0_SIZE (FLASH_LAYOUT_RUN0_SECTOR_SIZE * FLASH_LAYOUT_RUN0_SECTOR_COUNT)
#define MBOOT_DEV0_RUN0_ERASE FLASH_LAYOUT_RUN0_SECTOR_SIZE
#define MBOOT_DEV0_RUN1_SIZE (FLASH_LAYOUT_RUN1_SECTOR_SIZE * FLASH_LAYOUT_RUN1_SECTOR_COUNT)
#define MBOOT_DEV0_RUN1_ERASE FLASH_LAYOUT_RUN1_SECTOR_SIZE
#define MBOOT_DEV0_RUN2_SIZE (FLASH_LAYOUT_RUN2_SECTOR_SIZE * FLASH_LAYOUT_RUN2_SECTOR_COUNT)
#define MBOOT_DEV0_RUN2_ERASE FLASH_LAYOUT_RUN2_SECTOR_SIZE
#define MBOOT_DEV0_SIZE (MBOOT_DEV0_RUN0_SIZE + MBOOT_DEV0_RUN1_SIZE + MBOOT_DEV0_RUN2_SIZE)

// The table of flash.h lists the sectors of the larger parts of the series; a device with less
// flash (FLASH_END of the CMSIS header) is not described by it.
#if defined(FLASH_LAYOUT_RUN2_SECTOR_COUNT) && MBOOT_DEV0_BASE + MBOOT_DEV0_SIZE - 1 != FLASH_END
#error "mboot_dev.h: the sector table of ports/stm32/flash.h does not end at FLASH_END of this device"
#endif

// Two sectors of the first run for the bootloader, of which the last 64 bytes are its info
// block.
#ifndef MBOOT_BOOT_SIZE
#define MBOOT_BOOT_SIZE (2 * MBOOT_DEV0_RUN0_ERASE)
#endif
// The application's vector table must be aligned to this: the size of the device's vector table
// (16 core vectors and 110 interrupts of the STM32F76x), rounded up to a power of two.
#define MBOOT_VTOR_ALIGN 0x200u

#endif

#if defined(MBOOT_SPIFLASH_ADDR)
#if !defined(MBOOT_DEV1_SIZE)
#if defined(MBOOT_SPIFLASH_LAYOUT_DYNAMIC_MAX_LEN)
#error "mboot_dev.h: the SPI flash of this board is detected at run time (MBOOT_SPIFLASH_LAYOUT_DYNAMIC_MAX_LEN), so its size is not a constant that the layout can use; define MBOOT_DEV1_SIZE in mpconfigboard.h as the size that all its chips have"
#endif
#define MBOOT_DEV1_SIZE MBOOT_SPIFLASH_BYTE_SIZE
#endif
#define MBOOT_DEV1_NAME "spiflash"
#define MBOOT_DEV1_BASE MBOOT_SPIFLASH_ADDR
#define MBOOT_DEV1_ERASE (MBOOT_SPIFLASH_ERASE_BLOCKS_PER_PAGE * 4096u)
#define MBOOT_DEV1_WRITE MBOOT_DEV0_WRITE
#define MBOOT_DEV1_ERASED_VAL 0xff
#define MBOOT_DEV1_MAPPED 0
#define MBOOT_DEV1_ECC 0
#endif

// Linker symbols of mboot_sram.ld and mboot.ld: the request region the application writes
// for the bootloader, and the RAM an application may point the status word of a request at.
extern char mboot_req_start[];
extern char mboot_status_start[];
extern char mboot_status_end[];
#define MBOOT_REQ_START ((uintptr_t)mboot_req_start)
#define MBOOT_STATUS_RAM_START ((uintptr_t)mboot_status_start)
#define MBOOT_STATUS_RAM_END ((uintptr_t)mboot_status_end)

#endif // MICROPY_INCLUDED_STM32_MBOOT_MBOOT_DEV_H
