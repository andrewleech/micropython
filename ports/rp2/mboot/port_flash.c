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

// port_flash.c — real flash backend for the rp2 mboot bootloader.
//
// All flash-touching functions are RAM-resident (__not_in_flash_func) so that
// they can safely execute while the XIP controller is reconfigured by the
// pico-sdk boot ROM helpers inside flash_range_erase / flash_range_program.
//
// Interrupt safety: USB ISR fires from flash.  We must disable interrupts
// for the entire duration of any erase or program operation to prevent the
// ISR from fetching instructions from XIP while the flash controller is busy.

#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>
#include <string.h>

#include "hardware/flash.h"
#include "hardware/sync.h"
#include "hardware/regs/addressmap.h"

#include "mboot_api.h"
#include "mboot_board.h"

// Linker symbols exported by memmap_mboot_rp2040.ld / memmap_mboot_rp2350.ld.
extern uint32_t __mboot_start;
extern uint32_t __mboot_end;

// flash_range_program requires 256-byte alignment and a 256-byte-multiple
// length.  The common code only guarantees 4-byte alignment, so we stage
// each write through a 256-byte buffer aligned on a FLASH_PAGE_SIZE boundary.
static uint8_t s_page_buf[FLASH_PAGE_SIZE] __attribute__((aligned(FLASH_PAGE_SIZE)));

bool __not_in_flash_func(mboot_port_flash_is_writable)(mboot_addr_t addr, size_t len) {
    // Reject any range that overlaps with the bootloader slot.
    if (addr < (mboot_addr_t)&__mboot_end) {
        return false;
    }
    // Upper bound: addr + len must not exceed the end of flash.
    if ((uint32_t)addr + (uint32_t)len > XIP_BASE + PICO_FLASH_SIZE_BYTES) {
        return false;
    }
    return true;
}

int __not_in_flash_func(mboot_port_flash_page_erase)(mboot_addr_t addr, mboot_addr_t *next_addr) {
    // Round down to sector boundary.
    uint32_t sector_base = (uint32_t)addr & ~(FLASH_SECTOR_SIZE - 1u);
    uint32_t offset = sector_base - XIP_BASE;

    uint32_t save = save_and_disable_interrupts();
    flash_range_erase(offset, FLASH_SECTOR_SIZE);
    restore_interrupts(save);

    *next_addr = (mboot_addr_t)(sector_base + FLASH_SECTOR_SIZE);
    return 0;
}

int __not_in_flash_func(mboot_port_flash_write)(mboot_addr_t addr, const uint8_t *src, size_t len) {
    // Iterate in FLASH_PAGE_SIZE (256-byte) chunks.  Each chunk is staged
    // through s_page_buf to satisfy flash_range_program's alignment and size
    // requirements, and to handle a partial last page (padded with 0xFF).
    uint32_t dst = (uint32_t)addr;

    while (len > 0) {
        uint32_t page_base = dst & ~(FLASH_PAGE_SIZE - 1u);
        uint32_t page_off = dst - page_base;
        uint32_t chunk = FLASH_PAGE_SIZE - page_off;
        if (chunk > len) {
            chunk = len;
        }

        // Build a full 256-byte page: 0xFF for bytes we are not writing.
        memset(s_page_buf, 0xFF, FLASH_PAGE_SIZE);
        memcpy(s_page_buf + page_off, src, chunk);

        uint32_t offset = page_base - XIP_BASE;
        uint32_t save = save_and_disable_interrupts();
        flash_range_program(offset, s_page_buf, FLASH_PAGE_SIZE);
        restore_interrupts(save);

        dst += chunk;
        src += chunk;
        len -= chunk;
    }
    return 0;
}

int __not_in_flash_func(mboot_port_flash_read)(mboot_addr_t addr, uint8_t *dst, size_t len) {
    // XIP maps flash as ordinary memory; memcpy is sufficient.
    // flash_range_program flushes the XIP cache before re-enabling XIP, so
    // reads after a write see the newly programmed data without any extra flush.
    memcpy(dst, (const void *)(uint32_t)addr, len);
    return 0;
}
