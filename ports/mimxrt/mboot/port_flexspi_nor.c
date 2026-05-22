/*
 * This file is part of the MicroPython project, http://micropython.org/
 *
 * Based on NXP SDK examples:
 * Copyright (c) 2016, Freescale Semiconductor, Inc.
 * Copyright 2016-2020 NXP
 * SPDX-License-Identifier: BSD-3-Clause
 *
 * Port-specific adaptations:
 * The MIT License (MIT)
 * Copyright (c) 2021 Damien P. George
 * Copyright (c) 2025 Planet Innovation
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

// port_flexspi_nor.c — mboot-local FlexSPI NOR flash primitives.
//
// This is a vendored subset of ports/mimxrt/hal/flexspi_nor_flash.c containing
// only the erase-sector and page-program operations that mboot's port_flash.c
// requires.  The full HAL also provides quad-mode init, clock switching, and
// vendor-ID queries; none of those are needed in mboot.
//
// Every function is placed in .ram_functions.  While the FlexSPI controller is
// executing a write or erase command, the AHB bus to XIP flash is suspended and
// any instruction fetch from flash would cause a bus fault.  Executing from ITCM
// avoids this.  The FLEXSPI_TransferBlocking() driver function in fsl_flexspi.c
// is excluded from .text and pulled into .ram_functions by the linker script's
// EXCLUDE_FILE(*fsl_flexspi.o) clause.

#include "fsl_common.h"
#include "port_flexspi_nor.h"

// ---------------------------------------------------------------------------
// Internal helpers
// ---------------------------------------------------------------------------

__attribute__((section(".ram_functions")))
static void flexspi_nor_reset_internal(FLEXSPI_Type *base) {
    base->MCR0 |= FLEXSPI_MCR0_SWRESET_MASK;
    while (base->MCR0 & FLEXSPI_MCR0_SWRESET_MASK) {
    }
}

__attribute__((section(".ram_functions")))
static status_t flexspi_nor_write_enable(FLEXSPI_Type *base, uint32_t base_addr) {
    flexspi_transfer_t flashXfer;
    flashXfer.deviceAddress = base_addr;
    flashXfer.port = kFLEXSPI_PortA1;
    flashXfer.cmdType = kFLEXSPI_Command;
    flashXfer.SeqNumber = 1;
    flashXfer.seqIndex = NOR_CMD_LUT_SEQ_IDX_WRITEENABLE;
    return FLEXSPI_TransferBlocking(base, &flashXfer);
}

__attribute__((section(".ram_functions")))
static status_t flexspi_nor_wait_bus_busy(FLEXSPI_Type *base) {
    flexspi_transfer_t flashXfer;
    uint32_t read_value;
    bool is_busy;
    status_t status;

    flashXfer.deviceAddress = 0;
    flashXfer.port = kFLEXSPI_PortA1;
    flashXfer.cmdType = kFLEXSPI_Read;
    flashXfer.SeqNumber = 1;
    flashXfer.seqIndex = NOR_CMD_LUT_SEQ_IDX_READSTATUSREG;
    flashXfer.data = &read_value;
    flashXfer.dataSize = 1;

    do {
        status = FLEXSPI_TransferBlocking(base, &flashXfer);
        if (status != kStatus_Success) {
            return status;
        }
        // Busy flag polarity: 0 = busy when bit is set (standard WEL/WIP).
        if (read_value & (1U << FLASH_BUSY_STATUS_OFFSET)) {
            is_busy = (FLASH_BUSY_STATUS_POL == 0);
        } else {
            is_busy = (FLASH_BUSY_STATUS_POL != 0);
        }
    } while (is_busy);

    return kStatus_Success;
}

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

__attribute__((section(".ram_functions")))
status_t flexspi_nor_flash_erase_sector(FLEXSPI_Type *base, uint32_t address) {
    flexspi_transfer_t flashXfer;
    status_t status;

    status = flexspi_nor_write_enable(base, address);
    if (status != kStatus_Success) {
        return status;
    }

    flashXfer.deviceAddress = address;
    flashXfer.port = kFLEXSPI_PortA1;
    flashXfer.cmdType = kFLEXSPI_Command;
    flashXfer.SeqNumber = 1;
    flashXfer.seqIndex = NOR_CMD_LUT_SEQ_IDX_ERASESECTOR;
    status = FLEXSPI_TransferBlocking(base, &flashXfer);
    if (status != kStatus_Success) {
        return status;
    }

    status = flexspi_nor_wait_bus_busy(base);
    flexspi_nor_reset_internal(base);
    return status;
}

__attribute__((section(".ram_functions")))
status_t flexspi_nor_flash_page_program(FLEXSPI_Type *base, uint32_t address,
    const uint32_t *src, uint32_t size) {
    flexspi_transfer_t flashXfer;
    status_t status;

    status = flexspi_nor_write_enable(base, address);
    if (status != kStatus_Success) {
        return status;
    }

    flashXfer.deviceAddress = address;
    flashXfer.port = kFLEXSPI_PortA1;
    flashXfer.cmdType = kFLEXSPI_Write;
    flashXfer.SeqNumber = 1;
    flashXfer.seqIndex = NOR_CMD_LUT_SEQ_IDX_PAGEPROGRAM_QUAD;
    flashXfer.data = (uint32_t *)src;
    flashXfer.dataSize = size;
    status = FLEXSPI_TransferBlocking(base, &flashXfer);
    if (status != kStatus_Success) {
        return status;
    }

    status = flexspi_nor_wait_bus_busy(base);
    flexspi_nor_reset_internal(base);
    return status;
}
