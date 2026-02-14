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

// Shared H:4 byte-by-byte HCI packet parser and delivery helpers.
// Ports with standard UART HCI get RX parsing for free via the weak
// mp_bluetooth_zephyr_poll_uart() default. Non-standard transports
// (CYW43 SPI, STM32WB IPCC) override or bypass this module.

#ifndef MICROPY_INCLUDED_EXTMOD_ZEPHYR_BLE_HAL_ZEPHYR_BLE_H4_H
#define MICROPY_INCLUDED_EXTMOD_ZEPHYR_BLE_HAL_ZEPHYR_BLE_H4_H

#include <stdint.h>
#include <stddef.h>

struct net_buf;
struct device;
typedef int (*bt_hci_recv_t)(const struct device *, struct net_buf *);

// Register the Zephyr HCI device and receive callback for delivery helpers.
// Called from port's hci_open.
void mp_bluetooth_zephyr_h4_init(const struct device *dev, bt_hci_recv_t recv_cb);

// Clear stored device/callback. Called from port's hci_close.
void mp_bluetooth_zephyr_h4_deinit(void);

// Reset parser state, discarding any partial packet. Safe to call at any time.
void mp_bluetooth_zephyr_h4_reset(void);

// Feed one byte to the H:4 state machine. Returns a completed net_buf when
// a full packet has been assembled, NULL otherwise. Caller owns the returned
// buffer and is responsible for delivery or unref.
struct net_buf *mp_bluetooth_zephyr_h4_process_byte(uint8_t byte);

// Deliver a completed buffer via the registered recv_cb. Weak — ports that
// need deferred delivery (e.g. IRQ-safe queue) call process_byte directly
// and handle the buffer themselves.
void mp_bluetooth_zephyr_h4_deliver(struct net_buf *buf);

// Allocate a net_buf for the given H:4 packet type, copy data, and deliver
// via h4_deliver(). For transports that produce complete packets in a
// contiguous buffer. Returns 0 on success, -1 on allocation failure.
int mp_bluetooth_zephyr_hci_rx_packet(uint8_t pkt_type, const uint8_t *data, size_t len);

// Read bytes from mp_bluetooth_hci_uart_readchar() in a loop, feed each to
// the H:4 parser, deliver completed packets via h4_deliver(). Weak default
// for standard UART transports — overridden by CYW43 (SPI bulk reads).
void mp_bluetooth_zephyr_poll_uart(void);

#endif // MICROPY_INCLUDED_EXTMOD_ZEPHYR_BLE_HAL_ZEPHYR_BLE_H4_H
