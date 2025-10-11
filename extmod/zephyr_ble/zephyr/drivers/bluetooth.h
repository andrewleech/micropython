/*
 * This file is part of the MicroPython project, http://micropython.org/
 *
 * The MIT License (MIT)
 *
 * Copyright (c) 2025 MicroPython Contributors
 *
 * SPDX-License-Identifier: Apache-2.0
 */

// Wrapper for zephyr/drivers/bluetooth.h
// Provides HCI driver API without full device tree dependencies

#ifndef ZEPHYR_INCLUDE_DRIVERS_BLUETOOTH_H_
#define ZEPHYR_INCLUDE_DRIVERS_BLUETOOTH_H_

#include <errno.h>
#include "zephyr/device.h"

struct net_buf;

// HCI receive callback type (defined in zephyr_ble_config.h)
// typedef void (*bt_hci_recv_t)(const struct device *dev, struct net_buf *buf);

// HCI driver API structure (defined in zephyr_ble_config.h)
// struct bt_hci_driver_api { ... };

// HCI driver API wrapper functions
// These call through the device API structure

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

// Transport setup/teardown functions (defined by port)
int bt_hci_transport_setup(const struct device *dev);
int bt_hci_transport_teardown(const struct device *dev);

#endif // ZEPHYR_INCLUDE_DRIVERS_BLUETOOTH_H_
