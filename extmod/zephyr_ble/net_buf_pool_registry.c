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

// Provide linker symbols expected by Zephyr's pool lookup code
// These point to our registry array
// CRITICAL FIX: Use custom section name ".data.pool_list" to force both variables
// into the same named section, preventing -fdata-sections from separating them AND
// maintaining declaration order (GCC places variables within a custom section in
// declaration order, not alphabetically).
__attribute__((section(".data.pool_list"))) struct net_buf_pool **_net_buf_pool_list_start = registered_pools;
__attribute__((section(".data.pool_list"))) struct net_buf_pool **_net_buf_pool_list_end = registered_pools;

// Update end pointer after registration
// Should be called after all pools are registered
void mp_net_buf_pool_update_end(void) {
    _net_buf_pool_list_end = registered_pools + num_pools;
}

// ============================================================================
// Section list stubs for Zephyr STRUCT_SECTION_ITERABLE macros
// ============================================================================
// Zephyr uses linker sections to collect statically defined structures.
// Since we register callbacks dynamically, these lists are empty but the
// symbols must exist for the linker. Start and end point to same location.

// Forward declarations for Zephyr types
struct bt_conn_cb;
struct bt_l2cap_fixed_chan;
struct bt_gatt_service_static;

// Connection callbacks - registered dynamically via bt_conn_cb_register()
// Note: No const qualifier - Zephyr expects mutable section boundaries
__attribute__((section(".data.bt_conn_cb_list")))
struct bt_conn_cb *_bt_conn_cb_list_start = NULL;
__attribute__((section(".data.bt_conn_cb_list")))
struct bt_conn_cb *_bt_conn_cb_list_end = NULL;

// L2CAP fixed channels - registered dynamically
__attribute__((section(".data.bt_l2cap_fixed_chan_list")))
struct bt_l2cap_fixed_chan *_bt_l2cap_fixed_chan_list_start = NULL;
__attribute__((section(".data.bt_l2cap_fixed_chan_list")))
struct bt_l2cap_fixed_chan *_bt_l2cap_fixed_chan_list_end = NULL;

// GATT static services - registered dynamically via bt_gatt_service_register()
__attribute__((section(".data.bt_gatt_service_static_list")))
struct bt_gatt_service_static *_bt_gatt_service_static_list_start = NULL;
__attribute__((section(".data.bt_gatt_service_static_list")))
struct bt_gatt_service_static *_bt_gatt_service_static_list_end = NULL;
