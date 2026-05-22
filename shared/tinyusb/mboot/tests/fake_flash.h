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

#ifndef MBOOT_TESTS_FAKE_FLASH_H
#define MBOOT_TESTS_FAKE_FLASH_H

// fake_flash.h — in-memory implementation of mboot_port_flash_* callbacks.
//
// Tests register one or more fake_flash_t instances in the global registry
// before calling mboot_region_init().  The mboot_port_flash_* symbols defined
// in fake_flash.c walk the registry to dispatch each operation to the correct
// fake flash device.
//
// Usage:
//   static uint8_t buf[SIZE];
//   fake_flash_t ff;
//   fake_flash_init(&ff, BASE_ADDR, SIZE, SECTOR_SIZE);
//   fake_flash_register(&ff);
//   // ... run test ...
//   fake_flash_reset_registry();

#include <stddef.h>
#include <stdint.h>

#include "mboot_api.h"

// Maximum number of fake flash instances that can be registered at once.
#define FAKE_FLASH_MAX_INSTANCES (8)

// Maximum number of sectors tracked per flash instance for per-sector erase
// accounting.  Instances with more sectors use only the aggregate counter.
#define FAKE_FLASH_MAX_TRACKED_SECTORS (256)

// fake_flash_t — one in-memory flash device.
typedef struct {
    mboot_addr_t base;
    size_t size;
    uint32_t sector_size;
    uint8_t *bytes;          // caller-allocated backing store (size bytes)
    // Instrumentation counters (reset by fake_flash_reset_counters).
    unsigned int erase_calls;
    unsigned int write_calls;
    unsigned int read_calls;
    // Per-sector erase counts for the first FAKE_FLASH_MAX_TRACKED_SECTORS
    // sectors (zero-initialised by fake_flash_init; not reset by
    // fake_flash_reset_counters — call fake_flash_init to fully reset).
    unsigned int sector_erase_counts[FAKE_FLASH_MAX_TRACKED_SECTORS];
    // Whether this region is writable (mboot_port_flash_is_writable check).
    int writable; // 0 = not writable, non-zero = writable (bool semantics)
} fake_flash_t;

// fake_flash_init — initialise a fake_flash_t.
//
// bytes must point to a caller-allocated buffer of at least size bytes.
// The buffer is filled with 0xFF (erased state) on init.
// writable is set to 1 (writable) by default.
void fake_flash_init(fake_flash_t *ff, mboot_addr_t base, size_t size,
    uint32_t sector_size, uint8_t *bytes);

// fake_flash_reset_counters — zero the erase/write/read call counters.
void fake_flash_reset_counters(fake_flash_t *ff);

// fake_flash_register — add ff to the global registry.
//
// Must be called before mboot_region_init().  Up to FAKE_FLASH_MAX_INSTANCES
// may be registered at once.  Asserts (aborts) if the registry is full.
void fake_flash_register(fake_flash_t *ff);

// fake_flash_reset_registry — remove all entries from the global registry.
//
// Call between tests to start from a clean state.
void fake_flash_reset_registry(void);

// fake_flash_total_erases — sum erase_calls across all registered instances.
unsigned int fake_flash_total_erases(void);

// fake_flash_total_writes — sum write_calls across all registered instances.
unsigned int fake_flash_total_writes(void);

#endif // MBOOT_TESTS_FAKE_FLASH_H
