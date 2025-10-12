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

// Minimal Zephyr HCI driver for RP2 (CYW43 controller)
// Bridges between Zephyr's HCI driver API and CYW43 controller via WEAK overrides

#if MICROPY_PY_BLUETOOTH && MICROPY_BLUETOOTH_ZEPHYR

#include <zephyr/bluetooth/hci.h>
#include <zephyr/bluetooth/buf.h>
#include <zephyr/drivers/bluetooth.h>
#include "py/runtime.h"

// HCI receive callback (set by Zephyr's bt_enable)
static bt_hci_recv_t recv_cb = NULL;

// HCI open: Initialize transport and register receive callback
static int hci_open(const struct device *dev, bt_hci_recv_t recv) {
    (void)dev;
    mp_printf(&mp_plat_print, "HCI: hci_open called, recv=%p\n", recv);
    recv_cb = recv;

    // Controller already initialized by mp_bluetooth_hci_controller_init()
    // No additional setup needed - just store callback for receive path

    mp_printf(&mp_plat_print, "HCI: hci_open completed\n");
    return 0;
}

// HCI send: Send packet to CYW43 controller via WEAK overrides
static int hci_send(const struct device *dev, struct net_buf *buf) {
    (void)dev;

    // In Zephyr 4.2+, packet type is encoded as H:4 prefix byte at buf->data[0]
    // Buffer format: [pkt_type][...payload...]
    uint8_t pkt_type = buf->data[0];

    mp_printf(&mp_plat_print, "HCI: hci_send type=%u len=%u\n", pkt_type, buf->len);

    // Send via WEAK override (CYW43 driver provides implementation)
    // Buffer already has H:4 format, so just send as-is
    extern int mp_bluetooth_hci_uart_write(const uint8_t *buf, size_t len);
    int ret = mp_bluetooth_hci_uart_write(buf->data, buf->len);

    net_buf_unref(buf);

    if (ret != 0) {
        mp_printf(&mp_plat_print, "HCI ERROR: uart_write failed: %d\n", ret);
        return -1;
    }

    return 0;
}

// HCI close: Shutdown transport (optional)
static int hci_close(const struct device *dev) {
    (void)dev;
    mp_printf(&mp_plat_print, "HCI: hci_close called\n");
    recv_cb = NULL;
    return 0;
}

// HCI driver API structure
static const struct bt_hci_driver_api hci_driver_api = {
    .open = hci_open,
    .send = hci_send,
    .close = hci_close,
};

// HCI device structure (referenced by DEVICE_DT_GET macro)
const struct device mp_bluetooth_zephyr_hci_dev = {
    .name = "HCI_CYW43",
    .data = NULL,
    .api = &hci_driver_api,
};

// Function to process incoming HCI data (called from mpzephyrport.c polling)
// This reads from CYW43 via WEAK overrides and delivers to Zephyr
void mp_bluetooth_zephyr_poll_uart(void) {
    if (!recv_cb) {
        return;  // Not initialized yet
    }

    // Read HCI data from CYW43 via WEAK override
    extern int mp_bluetooth_hci_uart_readchar(void);
    int byte = mp_bluetooth_hci_uart_readchar();

    if (byte < 0) {
        return;  // No data available
    }

    // TODO: Implement full H:4 packet assembly and delivery to recv_cb
    // For now, just indicate data is available
    // This requires state machine to parse H:4 packets
}

#endif // MICROPY_PY_BLUETOOTH && MICROPY_BLUETOOTH_ZEPHYR
