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
#include <zephyr/sys/byteorder.h>
#include "py/runtime.h"

// HCI receive callback (set by Zephyr's bt_enable)
static bt_hci_recv_t recv_cb = NULL;

// H:4 packet parser state machine
static struct {
    struct net_buf *buf;           // Current packet buffer
    uint16_t remaining;            // Bytes remaining to read
    uint8_t type;                  // Current packet type (H:4)
    bool have_hdr;                 // Have we read the header?
    union {
        struct bt_hci_evt_hdr evt;
        struct bt_hci_acl_hdr acl;
        struct bt_hci_iso_hdr iso;
    } hdr;
} h4_rx;

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

// Reset H:4 parser state for next packet
static void reset_rx(void) {
    h4_rx.type = 0U;
    h4_rx.remaining = 0U;
    h4_rx.have_hdr = false;
    h4_rx.buf = NULL;
}

// Process one byte of HCI data through H:4 state machine
static void process_rx_byte(uint8_t byte) {
    // Step 1: Read packet type if starting new packet
    if (h4_rx.remaining == 0) {
        h4_rx.type = byte;

        switch (h4_rx.type) {
            case BT_HCI_H4_EVT:
                h4_rx.remaining = sizeof(struct bt_hci_evt_hdr);
                break;
            case BT_HCI_H4_ACL:
                h4_rx.remaining = sizeof(struct bt_hci_acl_hdr);
                break;
            case BT_HCI_H4_ISO:
                h4_rx.remaining = sizeof(struct bt_hci_iso_hdr);
                break;
            default:
                mp_printf(&mp_plat_print, "HCI RX: Unknown packet type 0x%02x\n", h4_rx.type);
                reset_rx();
                return;
        }

        h4_rx.have_hdr = false;
        return;
    }

    // Step 2: Read header bytes
    if (!h4_rx.have_hdr) {
        // Store byte in header union
        uint8_t *hdr_ptr = (uint8_t *)&h4_rx.hdr;
        size_t hdr_size = 0;

        switch (h4_rx.type) {
            case BT_HCI_H4_EVT:
                hdr_size = sizeof(struct bt_hci_evt_hdr);
                break;
            case BT_HCI_H4_ACL:
                hdr_size = sizeof(struct bt_hci_acl_hdr);
                break;
            case BT_HCI_H4_ISO:
                hdr_size = sizeof(struct bt_hci_iso_hdr);
                break;
        }

        size_t offset = hdr_size - h4_rx.remaining;
        hdr_ptr[offset] = byte;
        h4_rx.remaining--;

        // Check if header is complete
        if (h4_rx.remaining == 0) {
            h4_rx.have_hdr = true;

            // Parse header to get payload length
            switch (h4_rx.type) {
                case BT_HCI_H4_EVT:
                    h4_rx.remaining = h4_rx.hdr.evt.len;
                    break;
                case BT_HCI_H4_ACL: {
                    uint16_t len = sys_le16_to_cpu(h4_rx.hdr.acl.len);
                    h4_rx.remaining = len;
                    break;
                }
                case BT_HCI_H4_ISO: {
                    uint16_t len = sys_le16_to_cpu(h4_rx.hdr.iso.len) & 0x3FFF;
                    h4_rx.remaining = len;
                    break;
                }
            }

            // Allocate buffer based on packet type
            switch (h4_rx.type) {
                case BT_HCI_H4_EVT:
                    h4_rx.buf = bt_buf_get_evt(h4_rx.hdr.evt.evt, false, K_NO_WAIT);
                    break;
                case BT_HCI_H4_ACL:
                    h4_rx.buf = bt_buf_get_rx(BT_BUF_ACL_IN, K_NO_WAIT);
                    break;
                case BT_HCI_H4_ISO:
                    h4_rx.buf = bt_buf_get_rx(BT_BUF_ISO_IN, K_NO_WAIT);
                    break;
            }

            if (!h4_rx.buf) {
                mp_printf(&mp_plat_print, "HCI RX: Failed to allocate buffer\n");
                reset_rx();
                return;
            }

            // Add header to buffer (Zephyr expects buffer without H:4 type byte)
            net_buf_add_mem(h4_rx.buf, &h4_rx.hdr, hdr_size);

            // If no payload, deliver immediately
            if (h4_rx.remaining == 0) {
                struct net_buf *buf = h4_rx.buf;
                reset_rx();
                recv_cb(&mp_bluetooth_zephyr_hci_dev, buf);
            }
        }
        return;
    }

    // Step 3: Read payload bytes
    if (h4_rx.buf && h4_rx.remaining > 0) {
        net_buf_add_u8(h4_rx.buf, byte);
        h4_rx.remaining--;

        // Check if packet is complete
        if (h4_rx.remaining == 0) {
            struct net_buf *buf = h4_rx.buf;
            reset_rx();
            recv_cb(&mp_bluetooth_zephyr_hci_dev, buf);
        }
    }
}

// Function to process incoming HCI data (called from mpzephyrport.c polling)
// This reads from CYW43 via WEAK overrides and delivers to Zephyr
void mp_bluetooth_zephyr_poll_uart(void) {
    if (!recv_cb) {
        return;  // Not initialized yet
    }

    // Read HCI data from CYW43 via WEAK override
    extern int mp_bluetooth_hci_uart_readchar(void);

    // Process all available bytes (drain UART FIFO)
    while (true) {
        int byte = mp_bluetooth_hci_uart_readchar();

        if (byte < 0) {
            break;  // No more data available
        }

        process_rx_byte((uint8_t)byte);
    }
}

// Stub implementations for port-specific functions (not used by stub)
void mp_bluetooth_zephyr_port_init(void) {
    // No-op: stub doesn't need port initialization
}

void mp_bluetooth_zephyr_port_poll_in_ms(uint32_t ms) {
    (void)ms;
    // No-op: stub doesn't use soft timers
}

#endif // MICROPY_PY_BLUETOOTH && MICROPY_BLUETOOTH_ZEPHYR
