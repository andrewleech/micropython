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

// port_boot_data.c — mboot-local IVT and BootData structures.
//
// The SDK's fsl_flexspi_nor_boot.c hard-codes boot_data.length = FLASH_SIZE
// (the full flash, e.g. 16 MB for RT1176).  The boot ROM uses boot_data.length
// to determine how many bytes of the image to examine; using FLASH_SIZE forces
// it to scan the entire flash on every power-on.
//
// This file replaces the SDK version.  It defines the IVT and BootData
// structures inline, consuming the linker-exported _flashimagelen symbol so
// that boot_data.length equals the actual mboot image size, not the full flash.
//
// DCD is unconditionally excluded (XIP_BOOT_HEADER_DCD_ENABLE=0 in the mboot
// Makefile).  The IVT DCD field is set to 0.
//
// The struct layouts must match the i.MX RT boot ROM's expectations exactly.
// See: IMXRT1176RM §10.7, IMXRT1060RM §9.7, IMXRT1010RM §8.7.

#include <stdint.h>

// ---------------------------------------------------------------------------
// Linker symbols
// ---------------------------------------------------------------------------

// _flashimagelen is exported by each board's linker script as:
//   _flashimagelen = __FLASH_DATA_END - flash_start;
// It equals the number of bytes from the start of flash (FCB) to the end of
// the last data section copied out of flash (ram_functions).  The boot ROM
// reads exactly this many bytes; using FLASH_SIZE would force it to read the
// full flash.
extern uint32_t _flashimagelen;

// __FLASH_BASE is the linker-exported start address of the FCB section.
// On RT1176: FlexSPI1_AMBA_BASE + 0x400.
// On RT1052: FlexSPI_AMBA_BASE + 0x000.
// On RT1011: FlexSPI_AMBA_BASE + 0x400.
extern uint32_t __FLASH_BASE;

// __Vectors is the vector table used as IMAGE_ENTRY_ADDRESS by the IVT.
// Reset_Handler is at __Vectors[1] (offset 4), but the boot ROM jumps to the
// entry in the IVT's entry field, which should be Reset_Handler directly.
extern uint32_t Reset_Handler[];

// ---------------------------------------------------------------------------
// IVT — Image Vector Table
// ---------------------------------------------------------------------------
//
// Layout (8 x uint32_t = 32 bytes):
//   [0]  header   — tag 0xD1, length 0x2000, version 0x41 or 0x43
//   [1]  entry    — absolute address of Reset_Handler
//   [2]  reserved — 0
//   [3]  dcd      — 0 (DCD disabled)
//   [4]  boot_data — absolute address of the BootData struct below
//   [5]  self     — absolute address of this IVT struct
//   [6]  csf      — 0 (no HAB)
//   [7]  reserved — 0
//
// IVT_HEADER encoding: tag=0xD1 in bits[7:0], length=0x2000 in bits[23:8],
// version=0x41 in bits[31:24] (major=4, minor=1 for RT10xx/RT11xx family).

#define IVT_TAG_HEADER   0xD1U
#define IVT_LENGTH       0x2000U
#define IVT_VERSION      0x41U   // major=4, minor=1; matches all i.MX RT variants
#define IVT_HEADER_WORD  (IVT_TAG_HEADER | (IVT_LENGTH << 8) | (IVT_VERSION << 24))

// Forward declaration so the IVT self-pointer can reference this struct.
typedef struct {
    uint32_t hdr;
    uint32_t entry;
    uint32_t reserved1;
    uint32_t dcd;
    uint32_t boot_data;
    uint32_t self;
    uint32_t csf;
    uint32_t reserved2;
} mboot_ivt_t;

// Forward declaration of BootData so the IVT can take its address.
typedef struct {
    uint32_t start;
    uint32_t size;
    uint32_t plugin;
    uint32_t placeholder;
} mboot_boot_data_t;

__attribute__((section(".boot_hdr.boot_data"), used))
static const mboot_boot_data_t mboot_boot_data = {
    // start: beginning of the mboot flash image (FCB base).
    // __FLASH_BASE is the linker symbol for the FCB section start.
    .start = (uint32_t)&__FLASH_BASE,
    // size: actual image length from FCB base to end of last loaded section.
    // The boot ROM uses this to limit how much of flash it examines.
    // Cast via pointer: the linker exports _flashimagelen as a value, not an
    // address, so we take its address and dereference it as a uint32_t.
    .size = (uint32_t)&_flashimagelen,
    .plugin = 0U,
    .placeholder = 0xFFFFFFFFU,
};

__attribute__((section(".boot_hdr.ivt"), used))
static const mboot_ivt_t mboot_ivt = {
    .hdr = IVT_HEADER_WORD,
    .entry = (uint32_t)Reset_Handler,
    .reserved1 = 0U,
    .dcd = 0U,                             // DCD disabled (XIP_BOOT_HEADER_DCD_ENABLE=0)
    .boot_data = (uint32_t)&mboot_boot_data,
    .self = (uint32_t)&mboot_ivt,
    .csf = 0U,
    .reserved2 = 0U,
};
