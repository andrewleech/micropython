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
#include <string.h>

#include "spi_nor.h"

// The command set of drivers/memory/spiflash.c.
#define CMD_WRSR (0x01)
#define CMD_WRITE (0x02)
#define CMD_RDSR (0x05)
#define CMD_WREN (0x06)
#define CMD_SEC_ERASE (0x20)
#define CMD_RDCR (0x35)
#define CMD_RD_DEVID (0x9f)
#define CMD_C4READ (0xeb)
#define CMD_RSTEN (0x66)
#define CMD_RESET (0x99)
#define CMD_SLEEP (0xb9)
#define CMD_WAKE (0xab)
#define CMD_WRITE_32 (0x12)
#define CMD_SEC_ERASE_32 (0x21)
#define CMD_C4READ_32 (0xec)

#define SR_WEL (0x02)
#define CR_QE (0x02)
#define PAGE_SIZE (256)
#define SECTOR_SIZE (4096)
// Winbond W25Q128: manufacturer, memory type, capacity.
#define JEDEC_ID (0xef | (0x40 << 8) | (0x18 << 16))

void spi_nor_dev_cfg(fake_flash_dev_cfg_t *cfg, uint32_t size, bool scatter) {
    memset(cfg, 0, sizeof(*cfg));
    cfg->size = size;
    cfg->erase_unit = SECTOR_SIZE;
    cfg->write_unit = 1;
    cfg->erased_val = 0xff;
    cfg->tear_scatter = scatter;
}

static spi_nor_t *nor_of(void *self) {
    return (spi_nor_t *)self;
}

// What a chip does with a command it cannot execute: nothing. The caller learns of it as a
// violation of the fake flash.
static int refuse(void) {
    fake_flash_note_violation();
    return 0;
}

// A command outside a bus acquire, or while the chip sleeps, is not executed.
static bool accepts(spi_nor_t *n, uint8_t cmd) {
    return n->bus_depth != 0 && (!n->asleep || cmd == CMD_WAKE);
}

static int nor_ioctl(void *self, uint32_t cmd, uintptr_t arg) {
    spi_nor_t *n = nor_of(self);
    (void)arg;
    switch (cmd) {
        case MP_QSPI_IOCTL_INIT:
            n->bus_depth = 0;
            break;
        case MP_QSPI_IOCTL_BUS_ACQUIRE:
            if (n->bus_depth++ != 0) {
                refuse();
            }
            break;
        case MP_QSPI_IOCTL_BUS_RELEASE:
            if (n->bus_depth == 0) {
                refuse();
            } else {
                n->bus_depth--;
            }
            break;
        default:
            break;
    }
    return 0;
}

static int nor_write_cmd_data(void *self, uint8_t cmd, size_t len, uint32_t data) {
    spi_nor_t *n = nor_of(self);
    if (!accepts(n, cmd)) {
        return refuse();
    }
    switch (cmd) {
        case CMD_WREN:
            n->sr |= SR_WEL;
            return 0;
        case CMD_WRSR:
            if (!(n->sr & SR_WEL)) {
                return refuse();
            }
            if (len == 2) {
                n->cr = (data >> 8) & 0xff;
            }
            n->sr &= ~SR_WEL;
            return 0;
        case CMD_SLEEP:
            n->asleep = true;
            return 0;
        case CMD_WAKE:
            n->asleep = false;
            return 0;
        case CMD_RSTEN:
        case CMD_RESET:
            return 0;
        default:
            return -EINVAL;
    }
}

static int nor_write_cmd_addr_data(void *self, uint8_t cmd, uint32_t addr, size_t len, const uint8_t *src) {
    spi_nor_t *n = nor_of(self);
    if (!accepts(n, cmd) || !(n->sr & SR_WEL)) {
        return refuse();
    }
    bool a32 = cmd == CMD_WRITE_32 || cmd == CMD_SEC_ERASE_32;
    const fake_flash_dev_cfg_t *chip = fake_flash_dev_cfg(n->ff_dev);
    if (addr >= chip->size || (!a32 && addr >= (1u << 24))) {
        return refuse();
    }
    int rc;
    switch (cmd) {
        case CMD_WRITE:
        case CMD_WRITE_32:
            // The chip wraps inside the page: a program may not cross one.
            if (len == 0 || (addr & (PAGE_SIZE - 1)) + len > PAGE_SIZE) {
                return refuse();
            }
            rc = fake_flash_dev_write(n->ff_dev, addr, src, len);
            break;
        case CMD_SEC_ERASE:
        case CMD_SEC_ERASE_32:
            rc = fake_flash_dev_erase(n->ff_dev, addr & ~(SECTOR_SIZE - 1), SECTOR_SIZE);
            break;
        default:
            return -EINVAL;
    }
    n->sr &= ~SR_WEL;
    return rc;
}

static int nor_read_cmd(void *self, uint8_t cmd, size_t len, uint32_t *dest) {
    spi_nor_t *n = nor_of(self);
    if (!accepts(n, cmd)) {
        *dest = 0xffffffff;
        return refuse();
    }
    switch (cmd) {
        case CMD_RDSR:
            *dest = n->sr;
            return 0;
        case CMD_RDCR:
            *dest = n->cr;
            return 0;
        case CMD_RD_DEVID:
            *dest = JEDEC_ID;
            return 0;
        default:
            (void)len;
            return -EINVAL;
    }
}

static int nor_read_cmd_qaddr_qdata(void *self, uint8_t cmd, uint32_t addr, uint8_t num_dummy, size_t len, uint8_t *dest) {
    spi_nor_t *n = nor_of(self);
    const fake_flash_dev_cfg_t *chip = fake_flash_dev_cfg(n->ff_dev);
    memset(dest, 0xff, len);
    // The chip returns data on all four lines only with QE set, after two dummy bytes.
    if (!accepts(n, cmd) || !(n->cr & CR_QE) || num_dummy != 2 || (cmd != CMD_C4READ && cmd != CMD_C4READ_32)
        || addr > chip->size || len > chip->size - addr) {
        return refuse();
    }
    return fake_flash_dev_read(n->ff_dev, addr, dest, len);
}

static const mp_qspi_proto_t nor_proto = {
    .ioctl = nor_ioctl,
    .write_cmd_data = nor_write_cmd_data,
    .write_cmd_addr_data = nor_write_cmd_addr_data,
    .read_cmd = nor_read_cmd,
    .read_cmd_qaddr_qdata = nor_read_cmd_qaddr_qdata,
    .direct_read = NULL,
};

int spi_nor_init(spi_nor_t *nor, uint8_t ff_dev) {
    if (fake_flash_dev_cfg(ff_dev) == NULL) {
        return -ENODEV;
    }
    memset(nor, 0, sizeof(*nor));
    nor->ff_dev = ff_dev;
    nor->config.bus_kind = MP_SPIFLASH_BUS_QSPI;
    nor->config.bus.u_qspi.data = nor;
    nor->config.bus.u_qspi.proto = &nor_proto;
    nor->spif.config = &nor->config;
    mp_spiflash_init(&nor->spif);
    return 0;
}
