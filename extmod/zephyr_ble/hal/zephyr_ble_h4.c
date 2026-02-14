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

// Shared H:4 byte-by-byte HCI packet parser for Zephyr BLE integration.
// Handles EVT, ACL, and ISO packet types. Used by UART-based ports as a
// weak default; non-standard transports (CYW43, IPCC) override or bypass.

#include "py/mpconfig.h"

#if MICROPY_PY_BLUETOOTH && MICROPY_BLUETOOTH_ZEPHYR

#include "py/runtime.h"
#include "zephyr_ble_h4.h"

#include <zephyr/bluetooth/hci.h>
#include <zephyr/bluetooth/buf.h>
#include <zephyr/drivers/bluetooth.h>
#include <zephyr/sys/byteorder.h>

// Suppress deprecation warning for bt_buf_get_type()
#pragma GCC diagnostic push
#pragma GCC diagnostic ignored "-Wdeprecated-declarations"

// --- Transport registration ---

static const struct device *h4_dev = NULL;
static bt_hci_recv_t h4_recv_cb = NULL;

void mp_bluetooth_zephyr_h4_init(const struct device *dev, bt_hci_recv_t recv_cb) {
    h4_dev = dev;
    h4_recv_cb = recv_cb;
    mp_bluetooth_zephyr_h4_reset();
}

void mp_bluetooth_zephyr_h4_deinit(void) {
    mp_bluetooth_zephyr_h4_reset();
    h4_dev = NULL;
    h4_recv_cb = NULL;
}

// --- H:4 parser state ---

static struct {
    struct net_buf *buf;
    uint16_t remaining;
    uint8_t type;
    bool have_hdr;
    union {
        struct bt_hci_evt_hdr evt;
        struct bt_hci_acl_hdr acl;
        struct bt_hci_iso_hdr iso;
    } hdr;
} h4_rx;

void mp_bluetooth_zephyr_h4_reset(void) {
    if (h4_rx.buf) {
        net_buf_unref(h4_rx.buf);
    }
    h4_rx.type = 0;
    h4_rx.remaining = 0;
    h4_rx.have_hdr = false;
    h4_rx.buf = NULL;
}

struct net_buf *mp_bluetooth_zephyr_h4_process_byte(uint8_t byte) {
    // Step 1: Packet type byte — start of new packet
    if (h4_rx.type == 0) {
        switch (byte) {
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
                mp_printf(&mp_plat_print, "HCI ERROR: Unknown H:4 type 0x%02x\n", byte);
                return NULL;
        }
        h4_rx.type = byte;
        h4_rx.have_hdr = false;
        h4_rx.buf = NULL;
        return NULL;
    }

    // Step 2: Header bytes
    if (!h4_rx.have_hdr) {
        uint8_t *hdr_ptr = (uint8_t *)&h4_rx.hdr;
        size_t hdr_size;

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
            default:
                mp_bluetooth_zephyr_h4_reset();
                return NULL;
        }

        size_t offset = hdr_size - h4_rx.remaining;
        hdr_ptr[offset] = byte;
        h4_rx.remaining--;

        if (h4_rx.remaining > 0) {
            return NULL;
        }

        // Header complete — parse payload length and allocate buffer
        h4_rx.have_hdr = true;

        switch (h4_rx.type) {
            case BT_HCI_H4_EVT:
                h4_rx.remaining = h4_rx.hdr.evt.len;
                h4_rx.buf = bt_buf_get_evt(h4_rx.hdr.evt.evt, false, K_NO_WAIT);
                break;
            case BT_HCI_H4_ACL:
                h4_rx.remaining = sys_le16_to_cpu(h4_rx.hdr.acl.len);
                h4_rx.buf = bt_buf_get_rx(BT_BUF_ACL_IN, K_NO_WAIT);
                break;
            case BT_HCI_H4_ISO:
                h4_rx.remaining = sys_le16_to_cpu(h4_rx.hdr.iso.len) & 0x3FFF;
                h4_rx.buf = bt_buf_get_rx(BT_BUF_ISO_IN, K_NO_WAIT);
                break;
        }

        if (!h4_rx.buf) {
            mp_printf(&mp_plat_print, "HCI ERROR: Failed to allocate buffer for type 0x%02x\n", h4_rx.type);
            mp_bluetooth_zephyr_h4_reset();
            return NULL;
        }

        // Add header to buffer (Zephyr expects buffer without H:4 type byte)
        net_buf_add_mem(h4_rx.buf, &h4_rx.hdr, hdr_size);

        // Zero-length payload — packet is complete
        if (h4_rx.remaining == 0) {
            struct net_buf *buf = h4_rx.buf;
            h4_rx.buf = NULL;
            h4_rx.type = 0;
            return buf;
        }
        return NULL;
    }

    // Step 3: Payload bytes
    if (h4_rx.buf && h4_rx.remaining > 0) {
        net_buf_add_u8(h4_rx.buf, byte);
        h4_rx.remaining--;

        if (h4_rx.remaining == 0) {
            struct net_buf *buf = h4_rx.buf;
            h4_rx.buf = NULL;
            h4_rx.type = 0;
            return buf;
        }
    }

    return NULL;
}

// --- Delivery helpers ---

void mp_bluetooth_zephyr_h4_deliver(struct net_buf *buf) {
    if (h4_recv_cb && h4_dev) {
        int ret = h4_recv_cb(h4_dev, buf);
        if (ret < 0) {
            mp_printf(&mp_plat_print, "HCI ERROR: recv_cb failed: %d\n", ret);
            net_buf_unref(buf);
        }
    } else {
        net_buf_unref(buf);
    }
}

int mp_bluetooth_zephyr_hci_rx_packet(uint8_t pkt_type, const uint8_t *data, size_t len) {
    struct net_buf *buf = NULL;

    switch (pkt_type) {
        case BT_HCI_H4_EVT:
            if (len >= 1) {
                buf = bt_buf_get_evt(data[0], false, K_NO_WAIT);
            }
            break;
        case BT_HCI_H4_ACL:
            buf = bt_buf_get_rx(BT_BUF_ACL_IN, K_NO_WAIT);
            break;
        case BT_HCI_H4_ISO:
            buf = bt_buf_get_rx(BT_BUF_ISO_IN, K_NO_WAIT);
            break;
        default:
            return -1;
    }

    if (!buf) {
        return -1;
    }

    net_buf_add_mem(buf, data, len);
    mp_bluetooth_zephyr_h4_deliver(buf);
    return 0;
}

// --- Weak poll_uart default ---

__attribute__((weak))
void mp_bluetooth_zephyr_poll_uart(void) {
    if (!h4_recv_cb) {
        return;
    }

    extern int mp_bluetooth_hci_uart_readchar(void);

    while (true) {
        int byte = mp_bluetooth_hci_uart_readchar();
        if (byte < 0) {
            break;
        }

        struct net_buf *buf = mp_bluetooth_zephyr_h4_process_byte((uint8_t)byte);
        if (buf) {
            mp_bluetooth_zephyr_h4_deliver(buf);
        }
    }
}

#pragma GCC diagnostic pop

#endif // MICROPY_PY_BLUETOOTH && MICROPY_BLUETOOTH_ZEPHYR
