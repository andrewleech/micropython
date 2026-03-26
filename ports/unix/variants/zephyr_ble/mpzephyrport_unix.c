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

// Unix port integration for Zephyr BLE stack.
// Uses Linux HCI_CHANNEL_USER sockets for raw access to USB Bluetooth adapters.

#include "py/mpconfig.h"

#if MICROPY_PY_BLUETOOTH && MICROPY_BLUETOOTH_ZEPHYR

#include "py/runtime.h"
#include "py/mphal.h"
#include "extmod/modbluetooth.h"
#include "shared/runtime/softtimer.h"

#include <string.h>
#include <stdlib.h>
#include <unistd.h>
#include <errno.h>
#include <sys/socket.h>
#include <sys/ioctl.h>

// Suppress deprecation warnings for bt_buf_get_type()
#pragma GCC diagnostic push
#pragma GCC diagnostic ignored "-Wdeprecated-declarations"

#include "extmod/zephyr_ble/hal/zephyr_ble_hal.h"
#include "extmod/zephyr_ble/hal/zephyr_ble_poll.h"
#include "extmod/zephyr_ble/hal/zephyr_ble_port.h"

// Undef conflicting macros before including real Zephyr headers.
#undef CONTAINER_OF
#undef FLEXIBLE_ARRAY_DECLARE
#include "zephyr/device.h"
#include <zephyr/net_buf.h>
#include <zephyr/bluetooth/buf.h>
#include <zephyr/drivers/bluetooth.h>

// ============================================================================
// Inline Linux Bluetooth definitions (stable kernel ABI, no libbluetooth-dev)
// ============================================================================

#define AF_BLUETOOTH_LINUX 31
#define BTPROTO_HCI 1
#define HCI_CHANNEL_USER 1

#define HCIDEVDOWN  _IOW('H', 202, int)
#define HCIGETDEVLIST _IOR('H', 210, int)

struct sockaddr_hci {
    sa_family_t hci_family;
    unsigned short hci_dev;
    unsigned short hci_channel;
};

struct hci_dev_req {
    uint16_t dev_id;
    uint32_t dev_opt;
};

struct hci_dev_list_req {
    uint16_t dev_num;
    struct hci_dev_req dev_req[];
};

// H:4 packet type indicators
#define H4_CMD 0x01
#define H4_ACL 0x02
#define H4_EVT 0x04

// ============================================================================
// Debug output
// ============================================================================

#if ZEPHYR_BLE_DEBUG
#define debug_printf(...) mp_printf(&mp_plat_print, "mpzephyrport_unix: " __VA_ARGS__)
#else
#define debug_printf(...) do {} while (0)
#endif
#define error_printf(...) mp_printf(&mp_plat_print, "mpzephyrport_unix ERROR: " __VA_ARGS__)

// ============================================================================
// HCI socket state
// ============================================================================

static int hci_socket_fd = -1;
static volatile bt_hci_recv_t recv_cb = NULL;
static const struct device *hci_dev_ref = NULL;

// ============================================================================
// Device auto-detection
// ============================================================================

// Returns HCI device index, or -1 on error.
// Checks MICROPYBTHCI env var first, then enumerates devices.
static int hci_find_device(void) {
    // Check environment variable for explicit device index.
    const char *env = getenv("MICROPYBTHCI");
    if (env != NULL) {
        int idx = atoi(env);
        debug_printf("Using HCI device index %d from MICROPYBTHCI\n", idx);
        return idx;
    }

    // Enumerate devices via HCIGETDEVLIST ioctl.
    int ctl_fd = socket(AF_BLUETOOTH_LINUX, SOCK_RAW, BTPROTO_HCI);
    if (ctl_fd < 0) {
        error_printf("Failed to create HCI control socket: %s\n", strerror(errno));
        return -1;
    }

    // Allocate space for up to 16 devices.
    struct {
        struct hci_dev_list_req list;
        struct hci_dev_req devs[16];
    } dl;
    memset(&dl, 0, sizeof(dl));
    dl.list.dev_num = 16;

    if (ioctl(ctl_fd, HCIGETDEVLIST, &dl) < 0) {
        error_printf("HCIGETDEVLIST failed: %s\n", strerror(errno));
        close(ctl_fd);
        return -1;
    }

    close(ctl_fd);

    if (dl.list.dev_num == 0) {
        error_printf("No HCI devices found\n");
        return -1;
    }

    // Pick the first available device.
    debug_printf("Found %d HCI device(s), using hci%d\n",
        dl.list.dev_num, dl.list.dev_req[0].dev_id);
    return dl.list.dev_req[0].dev_id;
}

// ============================================================================
// Zephyr HCI driver API implementation
// ============================================================================

static int hci_unix_open(const struct device *dev, bt_hci_recv_t recv) {
    debug_printf("hci_unix_open called\n");

    int dev_id = hci_find_device();
    if (dev_id < 0) {
        return -1;
    }

    // Bring the device down before binding to HCI_CHANNEL_USER.
    // HCI_CHANNEL_USER requires the device to be down.
    int ctl_fd = socket(AF_BLUETOOTH_LINUX, SOCK_RAW, BTPROTO_HCI);
    if (ctl_fd < 0) {
        error_printf("Failed to create HCI control socket: %s\n", strerror(errno));
        return -1;
    }
    // Bring device down (ignore error -- may already be down).
    ioctl(ctl_fd, HCIDEVDOWN, dev_id);
    close(ctl_fd);

    // Create the HCI user channel socket.
    int fd = socket(AF_BLUETOOTH_LINUX, SOCK_RAW | SOCK_CLOEXEC | SOCK_NONBLOCK, BTPROTO_HCI);
    if (fd < 0) {
        error_printf("Failed to create HCI socket: %s\n", strerror(errno));
        return -1;
    }

    // Bind to HCI_CHANNEL_USER.
    struct sockaddr_hci addr;
    memset(&addr, 0, sizeof(addr));
    addr.hci_family = AF_BLUETOOTH_LINUX;
    addr.hci_dev = dev_id;
    addr.hci_channel = HCI_CHANNEL_USER;

    if (bind(fd, (struct sockaddr *)&addr, sizeof(addr)) < 0) {
        error_printf("Failed to bind HCI socket (hci%d): %s\n", dev_id, strerror(errno));
        error_printf("Ensure the device exists and is not in use by BlueZ (hciconfig hci%d down)\n", dev_id);
        close(fd);
        return -1;
    }

    hci_socket_fd = fd;
    hci_dev_ref = dev;
    recv_cb = recv;

    // Start the soft timer for periodic HCI polling.
    mp_bluetooth_zephyr_port_poll_in_ms(ZEPHYR_BLE_POLL_INTERVAL_MS);

    debug_printf("hci_unix_open completed, fd=%d\n", hci_socket_fd);
    return 0;
}

static int hci_unix_close(const struct device *dev) {
    (void)dev;
    debug_printf("hci_unix_close\n");

    recv_cb = NULL;
    mp_bluetooth_zephyr_poll_stop_timer();

    if (hci_socket_fd >= 0) {
        close(hci_socket_fd);
        hci_socket_fd = -1;
    }

    return 0;
}

static int hci_unix_send(const struct device *dev, struct net_buf *buf) {
    (void)dev;

    if (hci_socket_fd < 0) {
        net_buf_unref(buf);
        return -1;
    }

    // Determine H:4 packet type from buffer type.
    uint8_t h4_type;
    switch (bt_buf_get_type(buf)) {
        case BT_BUF_CMD:
            h4_type = H4_CMD;
            break;
        case BT_BUF_ACL_OUT:
            h4_type = H4_ACL;
            break;
        default:
            error_printf("Unknown buffer type: %u\n", bt_buf_get_type(buf));
            net_buf_unref(buf);
            return -1;
    }

    // Build H:4 packet: [type_byte | buf->data].
    // Stack-allocate to avoid heap pressure on every TX.
    size_t total_len = 1 + buf->len;
    uint8_t h4_pkt[1 + CONFIG_BT_BUF_ACL_TX_SIZE + 4]; // generous
    if (total_len > sizeof(h4_pkt)) {
        error_printf("TX packet too large: %zu\n", total_len);
        net_buf_unref(buf);
        return -1;
    }

    h4_pkt[0] = h4_type;
    memcpy(&h4_pkt[1], buf->data, buf->len);

    ssize_t ret = write(hci_socket_fd, h4_pkt, total_len);
    net_buf_unref(buf);

    if (ret < 0) {
        error_printf("HCI write failed: %s\n", strerror(errno));
        return -1;
    }

    return 0;
}

#pragma GCC diagnostic pop

// ============================================================================
// HCI device structure (required by Zephyr DEVICE_DT_GET macro)
// ============================================================================

static const struct bt_hci_driver_api hci_unix_api = {
    .open = hci_unix_open,
    .close = hci_unix_close,
    .send = hci_unix_send,
};

static struct device_state hci_device_state = {
    .init_res = 0,
    .initialized = true,
};

__attribute__((used))
const struct device __device_dts_ord_0 = {
    .name = "HCI_UNIX",
    .api = &hci_unix_api,
    .state = &hci_device_state,
    .data = NULL,
};

const struct device *const mp_bluetooth_zephyr_hci_dev = &__device_dts_ord_0;

// ============================================================================
// HCI transport setup/teardown (called by BLE host)
// ============================================================================

int bt_hci_transport_setup(const struct device *dev) {
    (void)dev;
    // No external controller to initialise for USB adapters.
    return 0;
}

int bt_hci_transport_teardown(const struct device *dev) {
    (void)dev;
    return 0;
}

// ============================================================================
// Port glue: strong overrides of weak defaults from zephyr_ble_poll.c
// ============================================================================

// External functions from modbluetooth_zephyr.c used during HCI processing.
extern void mp_bluetooth_zephyr_work_process(void);
extern void mp_bluetooth_zephyr_set_sys_work_q_context(bool in_context);
extern void mp_bluetooth_zephyr_l2cap_flush_recv_notify(void);

// HCI packet reception and processing -- called from soft timer via sched_node.
void mp_bluetooth_zephyr_port_run_task(mp_sched_node_t *node) {
    (void)node;

    if (hci_socket_fd < 0 || recv_cb == NULL || !mp_bluetooth_is_active()) {
        return;
    }

    // Process Zephyr BLE work queues and timers.
    mp_bluetooth_zephyr_poll();

    // Non-blocking read loop from HCI socket.
    // Each read() returns one complete HCI packet: [h4_type | hci_data].
    uint8_t rx_buf[4 + CONFIG_BT_BUF_ACL_RX_SIZE + 64];

    while (1) {
        // Check buffer availability before reading HCI data.
        if (!mp_bluetooth_zephyr_buffers_available()) {
            mp_bluetooth_zephyr_work_process();
            if (!mp_bluetooth_zephyr_buffers_available()) {
                break;
            }
        }

        ssize_t len = read(hci_socket_fd, rx_buf, sizeof(rx_buf));
        if (len <= 1) {
            break; // EAGAIN/EWOULDBLOCK or too short
        }

        // Parse H:4 type byte.
        uint8_t pkt_type = rx_buf[0];
        uint8_t *pkt_data = &rx_buf[1];
        size_t pkt_len = len - 1;

        struct net_buf *buf = NULL;

        switch (pkt_type) {
            case H4_EVT: {
                if (pkt_len < 2) {
                    continue;
                }
                uint8_t evt_code = pkt_data[0];
                // Command Complete (0x0E) and Command Status (0x0F) use
                // the dedicated command event pool.
                if (evt_code == 0x0E || evt_code == 0x0F) {
                    buf = bt_buf_get_evt(evt_code, false, K_NO_WAIT);
                } else {
                    buf = bt_buf_get_rx(BT_BUF_EVT, K_NO_WAIT);
                }
                break;
            }
            case H4_ACL:
                buf = bt_buf_get_rx(BT_BUF_ACL_IN, K_FOREVER);
                break;
            default:
                // Unknown type, skip.
                continue;
        }

        if (!buf) {
            continue;
        }

        net_buf_add_mem(buf, pkt_data, pkt_len);

        // Set work queue context so priority HCI events can process
        // TX notify directly instead of queuing work.
        mp_bluetooth_zephyr_set_sys_work_q_context(true);

        int ret = recv_cb(hci_dev_ref, buf);

        mp_bluetooth_zephyr_set_sys_work_q_context(false);

        if (ret < 0) {
            net_buf_unref(buf);
        }

        mp_bluetooth_zephyr_work_process();
    }

    // Flush deferred L2CAP recv notifications.
    mp_bluetooth_zephyr_l2cap_flush_recv_notify();

    // Reschedule soft timer for continuous HCI polling.
    mp_bluetooth_zephyr_port_poll_in_ms(ZEPHYR_BLE_POLL_INTERVAL_MS);
}

// Called by k_sem_take() to process HCI while waiting for a semaphore.
void mp_bluetooth_zephyr_hci_uart_wfi(void) {
    if (recv_cb == NULL || hci_socket_fd < 0) {
        return;
    }
    mp_bluetooth_zephyr_port_run_task(NULL);
}

// Main polling function.
void mp_bluetooth_hci_poll(void) {
    if (mp_bluetooth_is_active()) {
        // run_task reschedules itself at the end, no extra schedule needed.
        mp_bluetooth_zephyr_port_run_task(NULL);
    }
}

// Non-inline version for extern references from modbluetooth_zephyr.c.
void mp_bluetooth_hci_poll_now(void) {
    mp_bluetooth_zephyr_port_poll_now();
}

// Port init -- called during mp_bluetooth_init().
void mp_bluetooth_zephyr_port_init(void) {
    debug_printf("mp_bluetooth_zephyr_port_init\n");

    // Force linker to keep __device_dts_ord_0.
    volatile const void *keep_device = &__device_dts_ord_0;
    (void)keep_device;

    // Initialise soft timer infrastructure (idempotent).
    soft_timer_init();

    mp_bluetooth_zephyr_poll_init_timer();
}

// Port deinit -- called during mp_bluetooth_deinit().
void mp_bluetooth_zephyr_port_deinit(void) {
    debug_printf("mp_bluetooth_zephyr_port_deinit\n");

    recv_cb = NULL;

    mp_bluetooth_zephyr_poll_cleanup();
}

// ============================================================================
// Arch IRQ stubs -- no-ops on Unix (single-threaded BLE context)
// ============================================================================

unsigned int arch_irq_lock(void) {
    return 0;
}

void arch_irq_unlock(unsigned int key) {
    (void)key;
}

#endif // MICROPY_PY_BLUETOOTH && MICROPY_BLUETOOTH_ZEPHYR
