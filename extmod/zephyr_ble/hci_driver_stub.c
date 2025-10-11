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

// Minimal HCI driver stub for initial compilation testing
// This will be replaced with actual HCI UART integration in Phase 2

#include "zephyr/kernel.h"
#include <zephyr/net_buf.h>
// Forward declare arch_esf to satisfy hci_vs.h inclusion
struct arch_esf;
#include <zephyr/drivers/bluetooth.h>
#include <errno.h>

#define DEBUG_HCI_printf(...) // printf(__VA_ARGS__)

// Stub HCI driver state
static bt_hci_recv_t recv_cb = NULL;

// Stub HCI driver implementation
static int hci_stub_open(const struct device *dev, bt_hci_recv_t recv) {
    (void)dev;
    DEBUG_HCI_printf("hci_stub_open(%p, %p)\n", dev, recv);
    recv_cb = recv;
    return 0;
}

static int hci_stub_close(const struct device *dev) {
    (void)dev;
    DEBUG_HCI_printf("hci_stub_close(%p)\n", dev);
    recv_cb = NULL;
    return 0;
}

static int hci_stub_send(const struct device *dev, struct net_buf *buf) {
    (void)dev;
    DEBUG_HCI_printf("hci_stub_send(%p, %p) - dropping packet\n", dev, buf);
    // For now, just free the buffer
    // In Phase 2, this will forward to actual HCI UART
    net_buf_unref(buf);
    return 0;
}

// HCI driver API structure
static const struct bt_hci_driver_api hci_stub_api = {
    .open = hci_stub_open,
    .close = hci_stub_close,
    .send = hci_stub_send,
};

// HCI device structure
static const struct device hci_stub_dev = {
    .name = "HCI_STUB",
    .api = &hci_stub_api,
    .data = NULL,
};

// Get HCI device (called by Zephyr BLE host)
__attribute__((weak))
const struct device *bt_hci_get_device(void) {
    return &hci_stub_dev;
}

// HCI transport setup (called by BLE host during initialization)
__attribute__((weak))
int bt_hci_transport_setup(const struct device *dev) {
    DEBUG_HCI_printf("bt_hci_transport_setup(%p)\n", dev);
    (void)dev;
    return 0;
}

// HCI transport teardown
__attribute__((weak))
int bt_hci_transport_teardown(const struct device *dev) {
    DEBUG_HCI_printf("bt_hci_transport_teardown(%p)\n", dev);
    (void)dev;
    return 0;
}
