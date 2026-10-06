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

#include "mcuboot_port.h"
#include "flash_map_backend/flash_map_backend.h"
#include "sysflash/sysflash.h"
#include "host_env.h"

const mcuboot_flash_dev_t mcuboot_devs[] = {
    { .id = 0, .erased_val = 0xff, .mapped = 0, .write_unit = 16, .base = HOST_FS_BASE, .size = HOST_FS_SIZE,
      .run_count = 1, .runs = {{HOST_FS_SIZE, HOST_ERASE}}, .name = "fs" },
    { .id = 1, .erased_val = 0xff, .mapped = 0, .write_unit = 16, .base = HOST_SLOT_BASE, .size = HOST_SLOT_SIZE,
      .run_count = 1, .runs = {{HOST_SLOT_SIZE, HOST_ERASE}}, .name = "slots" },
};
const unsigned mcuboot_dev_count = 2;

const struct flash_area mcuboot_areas[] = {
    { .fa_id = FLASH_AREA_IMAGE_PRIMARY(0), .fa_device_id = 1, .fa_off = HOST_PRIMARY_OFF, .fa_size = HOST_PRIMARY_SIZE },
    #if !defined(MCUBOOT_SINGLE_APPLICATION_SLOT)
    { .fa_id = FLASH_AREA_IMAGE_SECONDARY(0), .fa_device_id = 1, .fa_off = HOST_SECONDARY_OFF, .fa_size = HOST_SECONDARY_SIZE },
    #endif
    { .fa_id = MCUBOOT_AREA_LOG, .fa_device_id = 1, .fa_off = HOST_LOG_OFF, .fa_size = HOST_LOG_SIZE },
    { .fa_id = MCUBOOT_AREA_INTENT, .fa_device_id = 1, .fa_off = HOST_INTENT_OFF, .fa_size = HOST_INTENT_SIZE },
};
const unsigned mcuboot_area_count = sizeof(mcuboot_areas) / sizeof(mcuboot_areas[0]);

static const uint8_t *fs_data;
static size_t fs_len;
static uint8_t slots[HOST_SLOT_SIZE];
static host_flash_stats_t stats;
static uint32_t ops;
static uint32_t cut_after = UINT32_MAX;

static uint32_t corrupt_read;
static uint32_t fs_read_count;

void host_fs_corrupt_read(uint32_t n) {
    corrupt_read = n;
    fs_read_count = 0;
}

void host_fs_set(const uint8_t *data, size_t len) {
    fs_data = data;
    fs_len = len;
}

void host_slots_reset(void) {
    memset(slots, 0xff, sizeof(slots));
    memset(&stats, 0, sizeof(stats));
}

uint8_t *host_slots(void) {
    return slots;
}

const host_flash_stats_t *host_flash_stats(void) {
    return &stats;
}

void host_flash_stats_clear(void) {
    memset(&stats, 0, sizeof(stats));
}

void host_flash_cut_after(uint32_t n) {
    ops = 0;
    cut_after = n;
}

void host_flash_power_on(void) {
    ops = 0;
    cut_after = UINT32_MAX;
}

uint32_t host_flash_ops(void) {
    return ops;
}

// Counts a write or erase call; false when the power cut has happened.
static bool power_ok(void) {
    ++ops;
    if (ops > cut_after) {
        stats.cut_ops++;
        return false;
    }
    return true;
}

static bool touches_log(uint32_t off, uint32_t len) {
    return off < HOST_LOG_OFF + HOST_LOG_SIZE && off + len > HOST_LOG_OFF;
}

int mcuboot_port_flash_init(void) {
    return 0;
}

int mcuboot_port_flash_read(uint8_t dev, uint32_t off, void *dst, uint32_t len) {
    if (dev == 0) {
        if (off > HOST_FS_SIZE || len > HOST_FS_SIZE - off) {
            return -EINVAL;
        }
        stats.fs_reads++;
        stats.fs_read_bytes += len;
        uint8_t *d = dst;
        for (uint32_t i = 0; i < len;) {
            if (off + i < fs_len) {
                uint32_t n = fs_len - (off + i);
                if (n > len - i) {
                    n = len - i;
                }
                memcpy(d + i, fs_data + off + i, n);
                i += n;
            } else {
                memset(d + i, 0xff, len - i);
                i = len;
            }
        }
        if (corrupt_read != 0 && ++fs_read_count == corrupt_read && len != 0) {
            d[0] = (uint8_t)~d[0];
        }
        return 0;
    }
    if (dev == 1 && off <= HOST_SLOT_SIZE && len <= HOST_SLOT_SIZE - off) {
        memcpy(dst, slots + off, len);
        return 0;
    }
    return -EINVAL;
}

int mcuboot_port_flash_write(uint8_t dev, uint32_t off, const void *src, uint32_t len) {
    if (dev != 1 || off > HOST_SLOT_SIZE || len > HOST_SLOT_SIZE - off || off % 16 != 0 || len % 16 != 0) {
        stats.violations++;
        return -EINVAL;
    }
    if (!power_ok()) {
        return -EIO;
    }
    stats.writes++;
    if (!touches_log(off, len)) {
        stats.writes_outside_log++;
    }
    for (uint32_t i = 0; i < len; ++i) {
        if (slots[off + i] != 0xff) {
            stats.violations++;
            return -EIO;
        }
    }
    memcpy(slots + off, src, len);
    return 0;
}

int mcuboot_port_flash_erase(uint8_t dev, uint32_t off, uint32_t len) {
    if (dev != 1 || off > HOST_SLOT_SIZE || len > HOST_SLOT_SIZE - off || off % HOST_ERASE != 0 || len % HOST_ERASE != 0) {
        stats.violations++;
        return -EINVAL;
    }
    if (!power_ok()) {
        return -EIO;
    }
    stats.erases++;
    if (!touches_log(off, len)) {
        stats.erases_outside_log++;
    }
    memset(slots + off, 0xff, len);
    return 0;
}

void mcuboot_port_wdt_feed(void) {
}
