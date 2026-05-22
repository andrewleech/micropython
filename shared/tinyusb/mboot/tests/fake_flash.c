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

// fake_flash.c — in-memory mboot_port_flash_* implementation for unit tests.

#include <errno.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "fake_flash.h"

// ---------------------------------------------------------------------------
// Global registry
// ---------------------------------------------------------------------------

static fake_flash_t *s_registry[FAKE_FLASH_MAX_INSTANCES];
static int s_registry_count;

// Find the fake_flash_t that covers addr.  Returns NULL if not found.
static fake_flash_t *find_ff(mboot_addr_t addr) {
    for (int i = 0; i < s_registry_count; ++i) {
        fake_flash_t *ff = s_registry[i];
        if (addr >= ff->base && addr < ff->base + (mboot_addr_t)ff->size) {
            return ff;
        }
    }
    return NULL;
}

// ---------------------------------------------------------------------------
// fake_flash_t lifecycle
// ---------------------------------------------------------------------------

void fake_flash_init(fake_flash_t *ff, mboot_addr_t base, size_t size,
    uint32_t sector_size, uint8_t *bytes) {
    ff->base = base;
    ff->size = size;
    ff->sector_size = sector_size;
    ff->bytes = bytes;
    ff->erase_calls = 0;
    ff->write_calls = 0;
    ff->read_calls = 0;
    ff->writable = 1;
    memset(ff->sector_erase_counts, 0, sizeof(ff->sector_erase_counts));
    memset(bytes, 0xFF, size);
}

void fake_flash_reset_counters(fake_flash_t *ff) {
    ff->erase_calls = 0;
    ff->write_calls = 0;
    ff->read_calls = 0;
}

void fake_flash_register(fake_flash_t *ff) {
    if (s_registry_count >= FAKE_FLASH_MAX_INSTANCES) {
        fprintf(stderr, "fake_flash: registry full\n");
        abort();
    }
    s_registry[s_registry_count++] = ff;
}

void fake_flash_reset_registry(void) {
    s_registry_count = 0;
}

unsigned int fake_flash_total_erases(void) {
    unsigned int total = 0;
    for (int i = 0; i < s_registry_count; ++i) {
        total += s_registry[i]->erase_calls;
    }
    return total;
}

unsigned int fake_flash_total_writes(void) {
    unsigned int total = 0;
    for (int i = 0; i < s_registry_count; ++i) {
        total += s_registry[i]->write_calls;
    }
    return total;
}

// ---------------------------------------------------------------------------
// mboot_api.h port callbacks — supplied to the linker by this translation unit
// ---------------------------------------------------------------------------

bool mboot_port_flash_is_writable(mboot_addr_t addr, size_t len) {
    for (int i = 0; i < s_registry_count; ++i) {
        fake_flash_t *ff = s_registry[i];
        if (addr >= ff->base && (mboot_addr_t)len <= (mboot_addr_t)ff->size &&
            addr <= ff->base + (mboot_addr_t)ff->size - (mboot_addr_t)len) {
            return ff->writable != 0;
        }
    }
    return false;
}

int mboot_port_flash_page_erase(mboot_addr_t addr, mboot_addr_t *next_addr) {
    fake_flash_t *ff = find_ff(addr);
    if (ff == NULL) {
        return -EINVAL;
    }
    // Round down to sector boundary.
    mboot_addr_t sector_start = (addr / ff->sector_size) * ff->sector_size;
    size_t offset = (size_t)(sector_start - ff->base);
    if (offset + ff->sector_size > ff->size) {
        return -EINVAL;
    }
    memset(ff->bytes + offset, 0xFF, ff->sector_size);
    ff->erase_calls++;
    // Record per-sector erase count for the first FAKE_FLASH_MAX_TRACKED_SECTORS.
    uint32_t sector_idx = (uint32_t)((sector_start - ff->base) / ff->sector_size);
    if (sector_idx < FAKE_FLASH_MAX_TRACKED_SECTORS) {
        ff->sector_erase_counts[sector_idx]++;
    }
    *next_addr = sector_start + ff->sector_size;
    return 0;
}

int mboot_port_flash_write(mboot_addr_t addr, const uint8_t *src, size_t len) {
    fake_flash_t *ff = find_ff(addr);
    if (ff == NULL) {
        return -EINVAL;
    }
    size_t offset = (size_t)(addr - ff->base);
    if (offset + len > ff->size) {
        return -EINVAL;
    }
    memcpy(ff->bytes + offset, src, len);
    ff->write_calls++;
    return 0;
}

int mboot_port_flash_read(mboot_addr_t addr, uint8_t *dst, size_t len) {
    fake_flash_t *ff = find_ff(addr);
    if (ff == NULL) {
        return -EINVAL;
    }
    size_t offset = (size_t)(addr - ff->base);
    if (offset + len > ff->size) {
        return -EINVAL;
    }
    memcpy(dst, ff->bytes + offset, len);
    ff->read_calls++;
    return 0;
}

// ---------------------------------------------------------------------------
// Unused port lifecycle callbacks — stubs required to satisfy the linker
// ---------------------------------------------------------------------------

void mboot_port_early_init(uint32_t *initial_r0) {
    (void)initial_r0;
}

bool mboot_port_entry_forced(void) {
    return false;
}

int mboot_port_get_reset_mode(void) {
    return 0;
}

void mboot_port_cleanup(int reset_mode) {
    (void)reset_mode;
}

void mboot_port_leave(int reset_mode) {
    (void)reset_mode;
    abort(); // should never be called in tests
}

void mboot_port_get_serial_number(char *buf, size_t buf_len) {
    if (buf_len > 0) {
        buf[0] = '\0';
    }
}

const char *mboot_port_get_product_string(void) {
    return "Test";
}

uint16_t mboot_port_get_vid(void) {
    return 0x1234;
}

uint16_t mboot_port_get_pid(void) {
    return 0x5678;
}

void mboot_port_led_init(void) {
}

void mboot_port_led_set(unsigned int led_mask) {
    (void)led_mask;
}

bool mboot_port_button_pressed(void) {
    return false;
}
