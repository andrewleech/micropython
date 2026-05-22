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

// port_flexspi_nor.h — mboot-local FlexSPI NOR flash declarations.
//
// This is a vendored subset of ports/mimxrt/hal/flexspi_nor_flash.h trimmed to
// only what mboot needs.  The original header includes mpconfigboard.h to pick
// up BOARD_FLASH_CONFIG_HEADER_H and the MICROPY_HW_FLASH_INTERNAL guard; mboot
// does not have mpconfigboard.h on its include path so that header cannot be
// used directly.
//
// BOARD_FLEX_SPI is derived here from the MCU family macros that the mboot
// Makefile already injects via -D:
//   MIMXRT117x_SERIES  — set for RT1176 (EVK)
//   (nothing)          — for RT10xx family (FLEXSPI, not FLEXSPI1/2)
//
// The qspiflash_config external is satisfied by the board's flash config object
// compiled from ports/mimxrt/hal/qspi_nor_flash_config.c.

#ifndef MICROPY_INCLUDED_MIMXRT_MBOOT_PORT_FLEXSPI_NOR_H
#define MICROPY_INCLUDED_MIMXRT_MBOOT_PORT_FLEXSPI_NOR_H

#include <stdint.h>
#include "fsl_flexspi.h"
#include "hal/flexspi_flash_config.h"

// Select the FlexSPI controller instance from the Makefile-injected family tag.
// Mirrors the three-way branch in ports/mimxrt/hal/flexspi_nor_flash.h.
#if defined(MICROPY_HW_FLASH_INTERNAL)
// Internal flash routed through FLEXSPI2 (not used by any Stage 1 board, but
// a future board that does will need this branch; the macro is off by default).
#define BOARD_FLEX_SPI FLEXSPI2
#define BOARD_FLEX_SPI_ADDR_BASE FlexSPI2_AMBA_BASE
#elif defined(MIMXRT117x_SERIES)
#define BOARD_FLEX_SPI FLEXSPI1
#define BOARD_FLEX_SPI_ADDR_BASE FlexSPI1_AMBA_BASE
#else
// RT1010/RT1020/RT1050/RT1060/RT1064 all expose a single FLEXSPI.
#define BOARD_FLEX_SPI FLEXSPI
#define BOARD_FLEX_SPI_ADDR_BASE FlexSPI_AMBA_BASE
#endif

// Defined in ports/mimxrt/hal/qspi_nor_flash_config.c (compiled into mboot).
extern flexspi_nor_config_t qspiflash_config;

// Flash operation functions — all placed in .ram_functions.
status_t flexspi_nor_flash_erase_sector(FLEXSPI_Type *base, uint32_t address);
status_t flexspi_nor_flash_page_program(FLEXSPI_Type *base, uint32_t address,
    const uint32_t *src, uint32_t size);

#endif // MICROPY_INCLUDED_MIMXRT_MBOOT_PORT_FLEXSPI_NOR_H
