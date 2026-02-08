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

#include "py/mphal.h"
#include "py/runtime.h"
#include "zephyr_ble_atomic.h"
#include <zephyr/kernel.h>
#include <errno.h>

// Debug flag (enabled after first successful print to avoid boot issues)
static volatile int debug_enabled = 0;

#define DEBUG_SLAB(fmt, ...) \
    do { \
        if (debug_enabled) { \
            mp_printf(&mp_plat_print, "[SLAB] " fmt "\n", ##__VA_ARGS__); \
        } \
    } while (0)

// Enable debug output
void mp_bluetooth_zephyr_mem_slab_enable_debug(void) {
    debug_enabled = 1;
    mp_printf(&mp_plat_print, "[SLAB] Debug output enabled\n");
}

// Initialize a memory slab (called by K_MEM_SLAB_DEFINE_STATIC macro)
void k_mem_slab_init(struct k_mem_slab *slab, void *buffer, size_t block_size, uint32_t num_blocks) {
    DEBUG_SLAB("k_mem_slab_init(%p, buffer=%p, block_size=%u, num_blocks=%u)",
               slab, buffer, (unsigned)block_size, (unsigned)num_blocks);

    slab->block_size = block_size;
    slab->num_blocks = num_blocks;
    slab->buffer = buffer;
    slab->free_list = NULL;
    slab->num_used = 0;

    // Build free list - each block points to the next
    char *block = (char *)buffer;
    for (uint32_t i = 0; i < num_blocks; i++) {
        void **next_ptr = (void **)block;
        *next_ptr = (i + 1 < num_blocks) ? (block + block_size) : NULL;
        block += block_size;
    }
    slab->free_list = buffer;

    DEBUG_SLAB("  -> free_list=%p, initialized %u blocks", slab->free_list, num_blocks);
}

// Allocate a block from the slab
int k_mem_slab_alloc(struct k_mem_slab *slab, void **mem, k_timeout_t timeout) {
    (void)timeout;  // Timeout not supported (always K_NO_WAIT behavior)

    if (!slab || !mem) {
        return -EINVAL;
    }

    MICROPY_PY_BLUETOOTH_ENTER

    // Lazy initialization: if free_list == buffer, initialize the slab
    if (slab->free_list == slab->buffer && slab->num_used == 0) {
        // Build free list - each block points to the next
        char *block = (char *)slab->buffer;
        for (uint32_t i = 0; i < slab->num_blocks; i++) {
            void **next_ptr = (void **)block;
            *next_ptr = (i + 1 < slab->num_blocks) ? (block + slab->block_size) : NULL;
            block += slab->block_size;
        }
        DEBUG_SLAB("k_mem_slab_alloc(%p): lazy init, block_size=%u, num_blocks=%u",
                   slab, (unsigned)slab->block_size, (unsigned)slab->num_blocks);
    }

    DEBUG_SLAB("k_mem_slab_alloc(%p, block_size=%u, used=%u/%u)",
               slab, (unsigned)slab->block_size, (unsigned)slab->num_used, (unsigned)slab->num_blocks);

    if (slab->free_list == NULL) {
        MICROPY_PY_BLUETOOTH_EXIT
        DEBUG_SLAB("  -> ENOMEM (no free blocks)");
        *mem = NULL;
        return -ENOMEM;
    }

    // Remove block from free list
    void *block = slab->free_list;
    void **next_ptr = (void **)block;
    slab->free_list = *next_ptr;
    slab->num_used++;

    MICROPY_PY_BLUETOOTH_EXIT

    *mem = block;
    DEBUG_SLAB("  -> allocated %p, free_list=%p, used=%u/%u",
               block, slab->free_list, (unsigned)slab->num_used, (unsigned)slab->num_blocks);
    return 0;
}

// Free a block back to the slab
void k_mem_slab_free(struct k_mem_slab *slab, void *mem) {
    if (!slab || !mem) {
        return;
    }

    DEBUG_SLAB("k_mem_slab_free(%p, mem=%p)", slab, mem);

    MICROPY_PY_BLUETOOTH_ENTER

    // Add block to head of free list
    void **next_ptr = (void **)mem;
    *next_ptr = slab->free_list;
    slab->free_list = mem;
    slab->num_used--;

    MICROPY_PY_BLUETOOTH_EXIT

    DEBUG_SLAB("  -> freed, free_list=%p, used=%u/%u",
               slab->free_list, (unsigned)slab->num_used, (unsigned)slab->num_blocks);
}
