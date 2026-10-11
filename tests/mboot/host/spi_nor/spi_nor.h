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

#ifndef MICROPY_INCLUDED_TESTS_MBOOT_HOST_SPI_NOR_SPI_NOR_H
#define MICROPY_INCLUDED_TESTS_MBOOT_HOST_SPI_NOR_SPI_NOR_H

// A SPI NOR flash chip behind the QSPI protocol of drivers/bus/qspi.h, so that the real
// drivers/memory/spiflash.c can drive it. Memory and power cut behaviour are device `ff_dev` of
// fake_flash.c: 4 KiB erase unit, write unit 1, erased 0xFF, no ECC. A sector erase command and a
// page program command are one fake_flash operation each, so the operation counting, the trace
// and the cut injection (a program cut leaves a torn byte, an erase cut a partly erased sector)
// apply to the chip like to any other device.
//
// The chip refuses what a real one does not do (it ignores a program or erase without write
// enable, a program that crosses a page, a quad read without the QE bit) and counts the refusal
// as a violation of fake_flash, so a driver misuse is reported by the sweeps.

#include <stdbool.h>
#include <stdint.h>

#include "fake_flash.h"
#include "drivers/memory/spiflash.h"

typedef struct {
    mp_spiflash_config_t config;
    mp_spiflash_t spif;
    uint8_t ff_dev;
    uint8_t sr;             // status register: bit 1 write enable latch, bit 0 busy (always 0)
    uint8_t cr;             // configuration register: bit 1 quad enable
    bool asleep;
    uint32_t bus_depth;
} spi_nor_t;

// Geometry of the chip as a fake_flash device. With scatter, a cut inside a command leaves the
// whole page or sector in an undefined state instead of only the bytes at the cut point.
void spi_nor_dev_cfg(fake_flash_dev_cfg_t *cfg, uint32_t size, bool scatter);

// Connects the model to fake_flash device ff_dev and runs mp_spiflash_init() on it, as a
// bootloader does at startup. The driver instance is nor->spif.
int spi_nor_init(spi_nor_t *nor, uint8_t ff_dev);

// port_flash.c: makes fake_flash device dev the SPI NOR chip of mboot_port_flash_*() (device 0
// stays plain fake flash) and initialises it. mboot_port_flash_dev_init() initialises it again.
// Call it after fake_flash_init(). Returns 0 or a negative errno.
int host_port_flash_attach_spi_nor(uint8_t dev);

#endif // MICROPY_INCLUDED_TESTS_MBOOT_HOST_SPI_NOR_SPI_NOR_H
