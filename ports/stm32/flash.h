/*
 * This file is part of the MicroPython project, http://micropython.org/
 *
 * The MIT License (MIT)
 *
 * Copyright (c) 2013, 2014 Damien P. George
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
#ifndef MICROPY_INCLUDED_STM32_FLASH_H
#define MICROPY_INCLUDED_STM32_FLASH_H

#include <stdbool.h>
#include <stdint.h>

// These series program 128 bits (a quad word) at a time, each with its own ECC. The STM32H7
// CMSIS headers define FLASH_NB_32BITWORD_IN_FLASHWORD (32 bit words per flash word); flash.c
// and the MCUboot port use the same name for H5 and U5.
//
// FLASH_WORD_BYTES is the size of one program unit and FLASH_WORD_HAS_ECC says whether each
// unit carries its own ECC.
#if defined(STM32H5) || defined(STM32U5)
#define FLASH_NB_32BITWORD_IN_FLASHWORD (4)
#define FLASH_WORD_BYTES (FLASH_NB_32BITWORD_IN_FLASHWORD * 4)
#define FLASH_WORD_HAS_ECC (1)
#endif

// The STM32F7 programs 32 bit words without ECC. The sectors of the larger devices (dual-bank
// mode disabled) are three runs of equal sectors, each described by its base address, sector
// size and sector count. flash.c and the MCUboot port read the same macros.
#if defined(STM32F7) && !(defined(STM32F722xx) || defined(STM32F723xx) || defined(STM32F732xx) || defined(STM32F733xx))
#define FLASH_WORD_BYTES (4)
#define FLASH_WORD_HAS_ECC (0)
#define FLASH_LAYOUT_RUN0_BASE (FLASH_BASE)
#define FLASH_LAYOUT_RUN0_SECTOR_SIZE (0x08000)
#define FLASH_LAYOUT_RUN0_SECTOR_COUNT (4)
#define FLASH_LAYOUT_RUN1_BASE (FLASH_LAYOUT_RUN0_BASE + FLASH_LAYOUT_RUN0_SECTOR_SIZE * FLASH_LAYOUT_RUN0_SECTOR_COUNT)
#define FLASH_LAYOUT_RUN1_SECTOR_SIZE (0x20000)
#define FLASH_LAYOUT_RUN1_SECTOR_COUNT (1)
#define FLASH_LAYOUT_RUN2_BASE (FLASH_LAYOUT_RUN1_BASE + FLASH_LAYOUT_RUN1_SECTOR_SIZE * FLASH_LAYOUT_RUN1_SECTOR_COUNT)
#define FLASH_LAYOUT_RUN2_SECTOR_SIZE (0x40000)
#if FLASH_SECTOR_TOTAL == 8
#define FLASH_LAYOUT_RUN2_SECTOR_COUNT (3)
#else
#define FLASH_LAYOUT_RUN2_SECTOR_COUNT (7)
#endif
#endif

bool flash_is_valid_addr(uint32_t addr);
uint32_t flash_get_max_sector_size(void);
int32_t flash_get_sector_info(uint32_t addr, uint32_t *start_addr, uint32_t *size);
int flash_erase(uint32_t flash_dest);
int flash_write(uint32_t flash_dest, const uint32_t *src, uint32_t num_word32);

#endif // MICROPY_INCLUDED_STM32_FLASH_H
