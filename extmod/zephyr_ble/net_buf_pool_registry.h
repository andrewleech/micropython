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

#ifndef MICROPY_INCLUDED_EXTMOD_ZEPHYR_BLE_NET_BUF_POOL_REGISTRY_H
#define MICROPY_INCLUDED_EXTMOD_ZEPHYR_BLE_NET_BUF_POOL_REGISTRY_H

// Forward declaration
struct net_buf_pool;

// Register a net_buf pool, returns pool ID or -1 on error
int mp_net_buf_pool_register(struct net_buf_pool *pool);

// Get number of registered pools
int mp_net_buf_pool_count(void);

// Get pool by ID (returns NULL if invalid)
struct net_buf_pool *mp_net_buf_pool_get(int id);

// Update linker symbols after registration complete
void mp_net_buf_pool_update_end(void);

// Reset registration (for testing)
void mp_net_buf_pool_reset(void);

#endif // MICROPY_INCLUDED_EXTMOD_ZEPHYR_BLE_NET_BUF_POOL_REGISTRY_H
