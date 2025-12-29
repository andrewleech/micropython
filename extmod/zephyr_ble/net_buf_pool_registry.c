/*
 * This file is part of the MicroPython project, http://micropython.org/
 *
 * The MIT License (MIT)
 *
 * Copyright (c) 2025 Damien P. George, Andrew Leech
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

// Dynamic net_buf pool registration system
//
// This file implements a workaround for the incompatibility between:
// 1. Zephyr's STRUCT_SECTION_ITERABLE linker section collection
// 2. MicroPython's use of GCC -fdata-sections flag
//
// The -fdata-sections flag overrides explicit __attribute__((section()))
// for static variables, causing Zephyr's pool collection to fail completely.
//
// Instead of fighting the build system, we use explicit runtime registration.
// This requires minimal patches to Zephyr sources (~10 lines) but works
// reliably across all ports and configurations.
//
// See docs/net_buf_pool_issue_analysis.md for full technical details.

#include <stdint.h>
#include <stddef.h>

// Forward declaration - actual struct is in zephyr/net_buf.h
struct net_buf_pool;

// Maximum number of net_buf pools
// Zephyr BLE typically uses 7-9 pools depending on configuration
#define MAX_NET_BUF_POOLS 16

// Pool registry
static struct net_buf_pool *registered_pools[MAX_NET_BUF_POOLS];
static uint8_t num_pools = 0;

// Register a net_buf pool and return its ID
// Called by Zephyr BLE during initialization
// Returns pool ID (>= 0) on success, -1 on failure
int mp_net_buf_pool_register(struct net_buf_pool *pool) {
    if (!pool) {
        return -1;
    }
    if (num_pools >= MAX_NET_BUF_POOLS) {
        return -1;  // Registry full
    }

    registered_pools[num_pools] = pool;
    return num_pools++;
}

// Get number of registered pools
int mp_net_buf_pool_count(void) {
    return num_pools;
}

// Get pool by ID
// Returns NULL if ID is invalid
struct net_buf_pool *mp_net_buf_pool_get(int id) {
    if (id < 0 || id >= num_pools) {
        return NULL;
    }
    return registered_pools[id];
}

// Reset registration (for testing)
void mp_net_buf_pool_reset(void) {
    num_pools = 0;
}

// REMOVED: These conflicting definitions are no longer needed.
//
// The linker script now properly defines _net_buf_pool_list_start and _net_buf_pool_list_end
// symbols by collecting all ._net_buf_pool.static.* sections. With -fno-data-sections applied
// to the Zephyr BLE sources, pools defined via NET_BUF_POOL_FIXED_DEFINE will be placed in
// the correct section and Zephyr's TYPE_SECTION_START/END macros will work correctly.
//
// Previously, these symbols pointed to our registry array (struct net_buf_pool **), but
// Zephyr expects them to be a contiguous array of pool structures (struct net_buf_pool[]).
// This type mismatch caused crashes when Zephyr tried to index into what it thought was
// an array of structures.
//
// The registry system (mp_net_buf_pool_register/get) is retained for potential future use
// if dynamic pool registration is needed, but is not currently used.

// ============================================================================
// Note on Zephyr iterable sections
// ============================================================================
// Zephyr uses linker sections to collect statically defined structures.
// The section boundaries (_<type>_list_start/end) are now defined in the
// linker scripts (memmap_mp_rp2040.ld, memmap_mp_rp2350.ld, common_extratext_data_in_flash.ld)
// rather than as C variables here.
//
// Previously, we defined these as pointers (struct foo *_list_start = NULL),
// but Zephyr's TYPE_SECTION_FOREACH expects them to be array symbols
// (extern struct foo _list_start[]) where the symbol IS the address.
// The linker script definitions handle this correctly.
