/*
 * This file is part of the MicroPython project, http://micropython.org/
 *
 * The MIT License (MIT)
 *
 * Copyright (c) 2025 MicroPython Contributors
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

// Simple bump allocator for GATT structures (malloc/free shims).
// Zephyr BLE GATT requires memory that persists outside the GC heap.
// This provides minimal malloc/free using a static pool. Memory is only
// truly freed on BLE deinit (gatt_pool_reset).
//
// Enable with MICROPY_BLUETOOTH_ZEPHYR_GATT_POOL=1 on ports without libc
// malloc/free (e.g. STM32 Zephyr builds that don't link libc).

#include "py/mpconfig.h"

#if MICROPY_PY_BLUETOOTH && MICROPY_BLUETOOTH_ZEPHYR && MICROPY_BLUETOOTH_ZEPHYR_GATT_POOL

#include <stddef.h>
#include <stdint.h>

#include "py/mpprint.h"

#define error_printf(...) mp_printf(&mp_plat_print, "zephyr_ble_gatt_alloc ERROR: " __VA_ARGS__)

#define GATT_POOL_SIZE 4096  // 4KB for GATT services/attributes

static uint8_t gatt_pool[GATT_POOL_SIZE];
static size_t gatt_pool_offset = 0;

// Simple allocation tracking for free() support
#define MAX_GATT_ALLOCS 64
static struct {
    void *ptr;
    size_t size;
} gatt_alloc_table[MAX_GATT_ALLOCS];
static int gatt_alloc_count = 0;

void *malloc(size_t size) {
    // Align to 4 bytes
    size = (size + 3) & ~3;

    if (gatt_pool_offset + size > GATT_POOL_SIZE) {
        error_printf("GATT pool exhausted (need %u, have %u)\n",
            (unsigned)size, (unsigned)(GATT_POOL_SIZE - gatt_pool_offset));
        return NULL;
    }

    void *ptr = &gatt_pool[gatt_pool_offset];
    gatt_pool_offset += size;

    // Track allocation for potential free()
    if (gatt_alloc_count < MAX_GATT_ALLOCS) {
        gatt_alloc_table[gatt_alloc_count].ptr = ptr;
        gatt_alloc_table[gatt_alloc_count].size = size;
        gatt_alloc_count++;
    }

    return ptr;
}

void free(void *ptr) {
    // In this bump allocator, individual frees don't reclaim memory.
    // Memory is only reclaimed on pool reset (BLE deinit).
    // We just mark the entry as freed for debugging.
    if (ptr == NULL) {
        return;
    }

    for (int i = 0; i < gatt_alloc_count; i++) {
        if (gatt_alloc_table[i].ptr == ptr) {
            gatt_alloc_table[i].ptr = NULL;  // Mark as freed
            return;
        }
    }
}

// Called during BLE deinit to reset the pool for next init cycle
void mp_bluetooth_zephyr_gatt_pool_reset(void) {
    gatt_pool_offset = 0;
    gatt_alloc_count = 0;
}

#endif // MICROPY_PY_BLUETOOTH && MICROPY_BLUETOOTH_ZEPHYR && MICROPY_BLUETOOTH_ZEPHYR_GATT_POOL
