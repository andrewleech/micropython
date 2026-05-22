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

// port_flash.c — real FlexSPI NOR flash backend for mimxrt mboot.
//
// All flash-touching functions are placed in the .ram_functions section so
// they execute from ITCM/DTCM while the FlexSPI AHB bus is suspended during
// erase and program operations.  This is required on all i.MX RT variants:
// code executing from XIP flash would fault when the FlexSPI controller is
// busy with a write command.
//
// Interrupt disable / re-enable wraps every flash operation to prevent the
// USB ISR (which runs from XIP) from being taken during an erase or write.
// The DCache is disabled across flash operations to prevent speculative
// reads of stale AHB-mapped data from triggering an AHB fault on RT1176.
//
// After each write or erase, FLEXSPI_SoftwareReset() is called to flush the
// FlexSPI AHB read buffer.  Without this, subsequent XIP reads (or an
// explicit mboot_port_flash_read) may return stale cached data.

#include <errno.h>
#include <string.h>

#include "fsl_common.h"

#include "port_flexspi_nor.h"

#include "mboot_api.h"
#include "mboot_board.h"

// ---------------------------------------------------------------------------
// Linker symbol: one past the last byte of the mboot image.
// ---------------------------------------------------------------------------

extern uint32_t __mboot_end;

// ---------------------------------------------------------------------------
// mboot_port_flash_is_writable
//
// Rejects any range that starts before __mboot_end (within the mboot slot) or
// extends beyond the end of the board's flash.  The __mboot_end linker symbol
// is set to the actual end of the mboot image, not the end of the mboot slot,
// so the application can begin immediately after the last byte of mboot code.
// ---------------------------------------------------------------------------

bool mboot_port_flash_is_writable(mboot_addr_t addr, size_t len) {
    uint32_t mboot_end = (uint32_t)&__mboot_end;
    if (addr < (mboot_addr_t)mboot_end) {
        return false;
    }
    if (addr + len > (mboot_addr_t)(MBOOT_FLASH_BASE + MICROPY_HW_FLASH_SIZE)) {
        return false;
    }
    return true;
}

// ---------------------------------------------------------------------------
// mboot_port_flash_page_erase
//
// Erases the 4 KiB sector that contains addr.  The FlexSPI offset passed to
// flexspi_nor_flash_erase_sector() is addr relative to MBOOT_FLASH_BASE; the
// sector base is rounded down by the SDK function internally.
//
// Interrupts are disabled for the duration to prevent the USB ISR from
// executing from XIP while the FlexSPI bus is busy.
//
// On return *next_addr points to the first byte past the erased sector.
// ---------------------------------------------------------------------------

__attribute__((section(".ram_functions")))
int mboot_port_flash_page_erase(mboot_addr_t addr, mboot_addr_t *next_addr) {
    uint32_t offset = (uint32_t)addr - MBOOT_FLASH_BASE;
    uint32_t sector_size = qspiflash_config.sectorSize;
    uint32_t sector_base = (offset / sector_size) * sector_size;

    uint32_t saved = DisableGlobalIRQ();
    SCB_DisableDCache();

    status_t st = flexspi_nor_flash_erase_sector(BOARD_FLEX_SPI, sector_base);

    // Flush the FlexSPI AHB read buffer so XIP reads after this erase see
    // the erased (0xFF) data rather than stale cached content.
    FLEXSPI_SoftwareReset(BOARD_FLEX_SPI);
    while (!FLEXSPI_GetBusIdleStatus(BOARD_FLEX_SPI)) {
    }

    SCB_EnableDCache();
    EnableGlobalIRQ(saved);

    if (st != kStatus_Success) {
        return -EIO;
    }

    *next_addr = (mboot_addr_t)(MBOOT_FLASH_BASE + sector_base + sector_size);
    return 0;
}

// ---------------------------------------------------------------------------
// mboot_port_flash_write
//
// Programs len bytes from src to flash at addr.  flexspi_nor_flash_page_program
// must not span a 256-byte page boundary, so this function iterates in chunks
// that fit within the current page.
//
// addr is guaranteed to be 4-byte aligned and len a multiple of 4 by the
// mboot_api.h contract, so the cast to uint32_t * is safe.
//
// Interrupts are disabled around each page-program call.  The AHB cache is
// flushed after the last chunk.
// ---------------------------------------------------------------------------

__attribute__((section(".ram_functions")))
int mboot_port_flash_write(mboot_addr_t addr, const uint8_t *src, size_t len) {
    uint32_t offset = (uint32_t)addr - MBOOT_FLASH_BASE;
    uint32_t page_size = qspiflash_config.pageSize;

    while (len > 0) {
        // Bytes remaining in the current page.
        uint32_t page_off = offset & (page_size - 1);
        size_t chunk = page_size - page_off;
        if (chunk > len) {
            chunk = len;
        }

        uint32_t saved = DisableGlobalIRQ();
        SCB_DisableDCache();

        status_t st = flexspi_nor_flash_page_program(
            BOARD_FLEX_SPI, offset, (const uint32_t *)src, (uint32_t)chunk);

        // Flush AHB cache after each page program so that the next XIP read
        // (or the loop's own pointer arithmetic) sees the written data.
        FLEXSPI_SoftwareReset(BOARD_FLEX_SPI);
        while (!FLEXSPI_GetBusIdleStatus(BOARD_FLEX_SPI)) {
        }

        SCB_EnableDCache();
        EnableGlobalIRQ(saved);

        if (st != kStatus_Success) {
            return -EIO;
        }

        offset += (uint32_t)chunk;
        src += chunk;
        len -= chunk;
    }
    return 0;
}

// ---------------------------------------------------------------------------
// mboot_port_flash_read
//
// Reads len bytes from XIP-mapped flash at addr into dst.  The FlexSPI AHB
// buffer is flushed first so that data written by a preceding
// mboot_port_flash_write call is visible.  This is mandatory when DFU_UPLOAD
// follows DFU_DNLOAD in the same session; the common code does not guarantee
// a flush between the two, so the flush is always applied here as a defensive
// measure.
//
// This function MUST reside in RAM (.ram_functions).  It issues
// FLEXSPI_SoftwareReset() on the same FlexSPI controller that is currently
// serving XIP instruction fetches.  The SWRESET cycle touches MCR0 on the
// active controller; if this function were in XIP flash, the CPU would fault
// mid-instruction when the AHB bus stalls during the reset cycle.
// ---------------------------------------------------------------------------

__attribute__((section(".ram_functions")))
int mboot_port_flash_read(mboot_addr_t addr, uint8_t *dst, size_t len) {
    uint32_t saved = DisableGlobalIRQ();
    SCB_DisableDCache();

    FLEXSPI_SoftwareReset(BOARD_FLEX_SPI);
    while (!FLEXSPI_GetBusIdleStatus(BOARD_FLEX_SPI)) {
    }
    memcpy(dst, (const void *)addr, len);

    SCB_EnableDCache();
    EnableGlobalIRQ(saved);
    return 0;
}
