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

#include <errno.h>

#include "fake_flash.h"
#include "mcuboot_spiflash.h"
#include "spi_nor.h"

// mcuboot_port_flash_*() of the host harnesses: device 0 is the fake flash; the device attached
// with host_port_flash_attach_spi_nor() goes through mcuboot_spiflash_*() (the backend a port
// uses), the real drivers/memory/spiflash.c and the chip model of spi_nor.c.

static spi_nor_t nor;
static int nor_dev = -1;

int host_port_flash_attach_spi_nor(uint8_t dev) {
    int rc = spi_nor_init(&nor, dev);
    nor_dev = rc == 0 ? dev : -1;
    return rc;
}

int mcuboot_port_flash_init(void) {
    return nor_dev >= 0 ? host_port_flash_attach_spi_nor(nor_dev) : 0;
}

int mcuboot_port_flash_read(uint8_t dev, uint32_t off, void *dst, uint32_t len) {
    if (dev == nor_dev) {
        return mcuboot_spiflash_read(&nor.spif, off, dst, len);
    }
    return fake_flash_dev_read(dev, off, dst, len);
}

int mcuboot_port_flash_write(uint8_t dev, uint32_t off, const void *src, uint32_t len) {
    if (dev == nor_dev) {
        return mcuboot_spiflash_write(&nor.spif, off, src, len);
    }
    return fake_flash_dev_write(dev, off, src, len);
}

int mcuboot_port_flash_erase(uint8_t dev, uint32_t off, uint32_t len) {
    if (dev == nor_dev) {
        return mcuboot_spiflash_erase(&nor.spif, off, len);
    }
    return fake_flash_dev_erase(dev, off, len);
}
