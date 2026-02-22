/*
 * This file is part of the MicroPython project, http://micropython.org/
 *
 * SPDX-License-Identifier: Apache-2.0
 *
 * Stub for zephyr/drivers/bluetooth.h
 *
 * Provides HCI driver API types and functions without including
 * the real Zephyr header (which has many dependencies).
 */

#ifndef MP_ZEPHYR_DRIVERS_BLUETOOTH_H_
#define MP_ZEPHYR_DRIVERS_BLUETOOTH_H_

#include <stdbool.h>
#include <stdint.h>
#include <zephyr/net_buf.h>
#include <zephyr/device.h>

#ifdef __cplusplus
extern "C" {
#endif

// HCI bus types (from Bluetooth Core Spec)
enum bt_hci_bus {
    BT_HCI_BUS_VIRTUAL = 0,
    BT_HCI_BUS_USB = 1,
    BT_HCI_BUS_PCCARD = 2,
    BT_HCI_BUS_UART = 3,
    BT_HCI_BUS_RS232 = 4,
    BT_HCI_BUS_PCI = 5,
    BT_HCI_BUS_SDIO = 6,
    BT_HCI_BUS_SPI = 7,
    BT_HCI_BUS_I2C = 8,
    BT_HCI_BUS_SMD = 9,
    BT_HCI_BUS_VIRTIO = 10,
    BT_HCI_BUS_IPC = 11,
};

// HCI quirks
enum {
    BT_HCI_QUIRK_NO_RESET = (1 << 0),
    BT_HCI_QUIRK_NO_AUTO_DLE = (1 << 1),
};

// HCI receive callback type
typedef int (*bt_hci_recv_t)(const struct device *dev, struct net_buf *buf);

// HCI driver API structure
struct bt_hci_driver_api {
    int (*open)(const struct device *dev, bt_hci_recv_t recv);
    int (*close)(const struct device *dev);
    int (*send)(const struct device *dev, struct net_buf *buf);
};

// Inline functions for HCI driver API

static inline int bt_hci_open(const struct device *dev, bt_hci_recv_t recv) {
    const struct bt_hci_driver_api *api = (const struct bt_hci_driver_api *)dev->api;
    return api->open(dev, recv);
}

static inline int bt_hci_close(const struct device *dev) {
    const struct bt_hci_driver_api *api = (const struct bt_hci_driver_api *)dev->api;
    if (api->close == NULL) {
        return -ENOSYS;
    }
    return api->close(dev);
}

static inline int bt_hci_send(const struct device *dev, struct net_buf *buf) {
    const struct bt_hci_driver_api *api = (const struct bt_hci_driver_api *)dev->api;
    return api->send(dev, buf);
}

// HCI command response buffer allocation (provided by host's hci_common.c)
// Used by the controller's hci.c to create command complete/status responses
struct net_buf *bt_hci_cmd_complete_create(uint16_t op, uint8_t plen);
struct net_buf *bt_hci_cmd_status_create(uint16_t op, uint8_t status);

// MicroPython-specific HCI transport setup/teardown functions
int bt_hci_transport_setup(const struct device *dev);
int bt_hci_transport_teardown(const struct device *dev);

#ifdef __cplusplus
}
#endif

#endif /* MP_ZEPHYR_DRIVERS_BLUETOOTH_H_ */
