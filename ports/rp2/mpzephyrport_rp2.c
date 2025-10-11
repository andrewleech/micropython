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

// RP2 port integration for Zephyr BLE stack with CYW43 controller

// Suppress deprecation warnings for bt_buf_get_type() - the function still works
// and we need it for H:4 HCI transport
#pragma GCC diagnostic push
#pragma GCC diagnostic ignored "-Wdeprecated-declarations"

// Include board configuration first to get all defines
#include "py/mpconfig.h"

#include "py/runtime.h"
#include "py/stream.h"
#include "py/mphal.h"
#include "extmod/modbluetooth.h"
#include "shared/runtime/softtimer.h"
#include "modmachine.h"

#if MICROPY_PY_BLUETOOTH && MICROPY_BLUETOOTH_ZEPHYR

// Forward declaration of machine UART type
extern const mp_obj_type_t machine_uart_type;

#include "extmod/zephyr_ble/hal/zephyr_ble_hal.h"
#include "zephyr/device.h"
#include <zephyr/net_buf.h>
#include <zephyr/bluetooth/buf.h>

// CYW43 driver for WiFi/BT chip
#if MICROPY_PY_NETWORK_CYW43
#include "lib/cyw43-driver/src/cyw43.h"
#endif

#define debug_printf(...) mp_printf(&mp_plat_print, "mpzephyrport_rp2: " __VA_ARGS__)
#define error_printf(...) mp_printf(&mp_plat_print, "mpzephyrport_rp2 ERROR: " __VA_ARGS__)

// UART interface for CYW43 HCI transport
#if defined(MICROPY_HW_BLE_UART_ID)

mp_obj_t mp_zephyr_uart;
static bt_hci_recv_t recv_cb = NULL;  // Returns int: 0 on success, negative on error
static const struct device *hci_dev = NULL;

// Soft timer for scheduling HCI poll
static soft_timer_entry_t mp_zephyr_hci_soft_timer;
static mp_sched_node_t mp_zephyr_hci_sched_node;

// Forward declarations
static void mp_zephyr_hci_poll_now(void);

// This is called by soft_timer and executes at PendSV level
static void mp_zephyr_hci_soft_timer_callback(soft_timer_entry_t *self) {
    mp_zephyr_hci_poll_now();
}

// HCI packet reception handler - called when data arrives from UART
static void run_zephyr_hci_task(mp_sched_node_t *node) {
    (void)node;

    // Process Zephyr BLE work queues and semaphores
    mp_bluetooth_zephyr_poll();

    // Check if UART has data available
    if (recv_cb == NULL) {
        return;
    }

    const mp_stream_p_t *proto = (mp_stream_p_t *)MP_OBJ_TYPE_GET_SLOT(&machine_uart_type, protocol);
    int errcode = 0;
    mp_uint_t ret = proto->ioctl(mp_zephyr_uart, MP_STREAM_POLL, MP_STREAM_POLL_RD, &errcode);

    if (!(ret & MP_STREAM_POLL_RD)) {
        return; // No data available
    }

    // Read packet type
    uint8_t pkt_type;
    if (proto->read(mp_zephyr_uart, &pkt_type, 1, &errcode) < 1) {
        return;
    }

    // Allocate buffer based on packet type
    struct net_buf *buf = NULL;
    uint16_t remaining = 0;

    switch (pkt_type) {
        case BT_HCI_H4_EVT: {
            // Read event header (2 bytes)
            uint8_t hdr[2];
            if (proto->read(mp_zephyr_uart, hdr, 2, &errcode) < 2) {
                return;
            }
            remaining = hdr[1]; // param length
            buf = bt_buf_get_evt(hdr[0], false, K_NO_WAIT);
            if (buf) {
                net_buf_add_mem(buf, hdr, 2);
            }
            break;
        }
        case BT_HCI_H4_ACL: {
            // Read ACL header (4 bytes)
            uint8_t hdr[4];
            if (proto->read(mp_zephyr_uart, hdr, 4, &errcode) < 4) {
                return;
            }
            remaining = hdr[2] | (hdr[3] << 8); // data length
            buf = bt_buf_get_rx(BT_BUF_ACL_IN, K_NO_WAIT);
            if (buf) {
                net_buf_add_mem(buf, hdr, 4);
            }
            break;
        }
        default:
            error_printf("Unknown HCI packet type: 0x%02x\n", pkt_type);
            return;
    }

    if (!buf) {
        error_printf("Failed to allocate buffer for HCI packet\n");
        // Drain remaining bytes
        uint8_t dummy;
        for (uint16_t i = 0; i < remaining; i++) {
            proto->read(mp_zephyr_uart, &dummy, 1, &errcode);
        }
        return;
    }

    // Read remaining packet data
    if (remaining > 0) {
        uint8_t *data = net_buf_add(buf, remaining);
        if (proto->read(mp_zephyr_uart, data, remaining, &errcode) < remaining) {
            net_buf_unref(buf);
            return;
        }
    }

    // Pass buffer to Zephyr BLE stack
    int recv_ret = recv_cb(hci_dev, buf);
    if (recv_ret < 0) {
        error_printf("recv_cb failed: %d\n", recv_ret);
        net_buf_unref(buf);
    }
}

static void mp_zephyr_hci_poll_now(void) {
    mp_sched_schedule_node(&mp_zephyr_hci_sched_node, run_zephyr_hci_task);
}

// Zephyr HCI driver implementation

static int hci_cyw43_open(const struct device *dev, bt_hci_recv_t recv) {
    mp_printf(&mp_plat_print, "=== HCI: hci_cyw43_open called, dev=%p recv=%p\n", dev, recv);
    hci_dev = dev;
    recv_cb = recv;

    // Initialize UART for HCI
    mp_printf(&mp_plat_print, "=== HCI: Initializing UART%d for HCI\n", MICROPY_HW_BLE_UART_ID);
    mp_obj_t args[] = {
        MP_OBJ_NEW_SMALL_INT(MICROPY_HW_BLE_UART_ID),
        MP_OBJ_NEW_QSTR(MP_QSTR_baudrate), MP_OBJ_NEW_SMALL_INT(115200),
        MP_OBJ_NEW_QSTR(MP_QSTR_flow), MP_OBJ_NEW_SMALL_INT((1 | 2)), // RTS|CTS
        MP_OBJ_NEW_QSTR(MP_QSTR_timeout), MP_OBJ_NEW_SMALL_INT(1000),
        MP_OBJ_NEW_QSTR(MP_QSTR_timeout_char), MP_OBJ_NEW_SMALL_INT(200),
        MP_OBJ_NEW_QSTR(MP_QSTR_rxbuf), MP_OBJ_NEW_SMALL_INT(768),
    };

    mp_printf(&mp_plat_print, "=== HCI: Creating UART object\n");
    mp_zephyr_uart = MP_OBJ_TYPE_GET_SLOT(&machine_uart_type, make_new)(
        (mp_obj_t)&machine_uart_type, 1, 5, args);
    mp_printf(&mp_plat_print, "=== HCI: UART object created\n");

    // Start polling
    mp_printf(&mp_plat_print, "=== HCI: Starting HCI polling\n");
    mp_zephyr_hci_poll_now();

    mp_printf(&mp_plat_print, "=== HCI: hci_cyw43_open completed\n");
    return 0;
}

static int hci_cyw43_close(const struct device *dev) {
    (void)dev;
    debug_printf("hci_cyw43_close\n");
    recv_cb = NULL;
    soft_timer_remove(&mp_zephyr_hci_soft_timer);
    return 0;
}

static int hci_cyw43_send(const struct device *dev, struct net_buf *buf) {
    (void)dev;
    debug_printf("hci_cyw43_send: type=%u len=%u\n", bt_buf_get_type(buf), buf->len);

    const mp_stream_p_t *proto = (mp_stream_p_t *)MP_OBJ_TYPE_GET_SLOT(&machine_uart_type, protocol);
    int errcode = 0;

    // Write packet type indicator
    uint8_t pkt_type;
    switch (bt_buf_get_type(buf)) {
        case BT_BUF_CMD:
            pkt_type = BT_HCI_H4_CMD;
            break;
        case BT_BUF_ACL_OUT:
            pkt_type = BT_HCI_H4_ACL;
            break;
        default:
            error_printf("Unknown buffer type: %u\n", bt_buf_get_type(buf));
            net_buf_unref(buf);
            return -1;
    }

    if (proto->write(mp_zephyr_uart, &pkt_type, 1, &errcode) < 1) {
        error_printf("Failed to write HCI packet type\n");
        net_buf_unref(buf);
        return -1;
    }

    // Write packet data
    if (proto->write(mp_zephyr_uart, buf->data, buf->len, &errcode) < 0) {
        error_printf("Failed to write HCI packet data\n");
        net_buf_unref(buf);
        return -1;
    }

    net_buf_unref(buf);
    return 0;
}

// HCI driver API structure
static const struct bt_hci_driver_api hci_cyw43_api = {
    .open = hci_cyw43_open,
    .close = hci_cyw43_close,
    .send = hci_cyw43_send,
};

// HCI device structure (referenced by Zephyr via DEVICE_DT_GET macro)
const struct device mp_bluetooth_zephyr_hci_dev = {
    .name = "HCI_CYW43",
    .api = &hci_cyw43_api,
    .data = NULL,
};

// UART HAL functions for cyw43_bthci_uart.c BT controller initialization
// These bridge to machine.UART module

int cyw43_hal_uart_readchar(void) {
    if (mp_zephyr_uart == MP_OBJ_NULL) {
        return -1;
    }
    const mp_stream_p_t *proto = (mp_stream_p_t *)MP_OBJ_TYPE_GET_SLOT(&machine_uart_type, protocol);
    int errcode = 0;
    mp_uint_t ret = proto->ioctl(mp_zephyr_uart, MP_STREAM_POLL, MP_STREAM_POLL_RD, &errcode);
    if (!(ret & MP_STREAM_POLL_RD)) {
        return -1;
    }
    uint8_t c;
    if (proto->read(mp_zephyr_uart, &c, 1, &errcode) < 1) {
        return -1;
    }
    return c;
}

void cyw43_hal_uart_write(const void *buf, size_t len) {
    if (mp_zephyr_uart == MP_OBJ_NULL) {
        return;
    }
    const mp_stream_p_t *proto = (mp_stream_p_t *)MP_OBJ_TYPE_GET_SLOT(&machine_uart_type, protocol);
    int errcode = 0;
    proto->write(mp_zephyr_uart, buf, len, &errcode);
}

void cyw43_hal_uart_set_baudrate(uint32_t baudrate) {
    // Baudrate is set during UART initialization
    // For now, we don't support changing it dynamically
    (void)baudrate;
}

// HCI transport setup (called by BLE host during initialization)
int bt_hci_transport_setup(const struct device *dev) {
    mp_printf(&mp_plat_print, "=== HCI: bt_hci_transport_setup called\n");
    (void)dev;

    #if MICROPY_PY_NETWORK_CYW43
    // Check if CYW43 driver is initialized (WiFi side)
    extern cyw43_t cyw43_state;
    bool cyw43_init = cyw43_is_initialized(&cyw43_state);
    mp_printf(&mp_plat_print, "=== HCI: CYW43 driver initialized: %d\n", cyw43_init);

    if (!cyw43_init) {
        mp_printf(&mp_plat_print, "=== HCI: Initializing CYW43 driver first\n");
        extern int cyw43_arch_init(void);
        int ret = cyw43_arch_init();
        if (ret != 0) {
            mp_printf(&mp_plat_print, "=== HCI ERROR: cyw43_arch_init failed: %d\n", ret);
            return ret;
        }
        mp_printf(&mp_plat_print, "=== HCI: CYW43 driver initialized successfully\n");
    }
    #endif

    // Initialize CYW43 BT controller using cyw43_bthci_uart.c
    mp_printf(&mp_plat_print, "=== HCI: Calling cyw43_bluetooth_controller_init\n");
    extern int cyw43_bluetooth_controller_init(void);
    int ret = cyw43_bluetooth_controller_init();
    if (ret != 0) {
        mp_printf(&mp_plat_print, "=== HCI ERROR: cyw43_bluetooth_controller_init failed: %d\n", ret);
        return ret;
    }

    mp_printf(&mp_plat_print, "=== HCI: bt_hci_transport_setup completed successfully\n");
    return 0;
}

// HCI transport teardown
int bt_hci_transport_teardown(const struct device *dev) {
    debug_printf("bt_hci_transport_teardown\n");
    (void)dev;

    // TODO: De-initialize CYW43 BT controller
    // Would power down the BT controller here

    return 0;
}

// Initialize Zephyr port
void mp_bluetooth_zephyr_port_init(void) {
    soft_timer_static_init(
        &mp_zephyr_hci_soft_timer,
        SOFT_TIMER_MODE_ONE_SHOT,
        0,
        mp_zephyr_hci_soft_timer_callback
        );
}

// Schedule HCI poll
void mp_bluetooth_zephyr_port_poll_in_ms(uint32_t ms) {
    soft_timer_reinsert(&mp_zephyr_hci_soft_timer, ms);
}

#else // !defined(MICROPY_HW_BLE_UART_ID)

// Stub implementations for ports without UART HCI
void mp_bluetooth_zephyr_port_init(void) {
}

void mp_bluetooth_zephyr_port_poll_in_ms(uint32_t ms) {
    (void)ms;
}

#endif // defined(MICROPY_HW_BLE_UART_ID)

#endif // MICROPY_PY_BLUETOOTH && MICROPY_BLUETOOTH_ZEPHYR
