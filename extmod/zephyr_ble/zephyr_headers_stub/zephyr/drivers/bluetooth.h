/*
 * This file is part of the MicroPython project, http://micropython.org/
 *
 * SPDX-License-Identifier: Apache-2.0
 *
 * Wrapper for zephyr/drivers/bluetooth.h
 *
 * This wrapper re-exports the definitions from the real Zephyr header
 * and provides any additional definitions needed for MicroPython.
 */

#ifndef MP_ZEPHYR_DRIVERS_BLUETOOTH_WRAPPER_H_
#define MP_ZEPHYR_DRIVERS_BLUETOOTH_WRAPPER_H_

// Include Bluetooth address types (needed by drivers/bluetooth.h)
#include <zephyr/bluetooth/addr.h>

// Include the real Zephyr drivers/bluetooth.h from lib/zephyr
// Use a relative path from extmod/zephyr_ble/zephyr_headers_stub/zephyr/drivers/
#include "../../../lib/zephyr/include/zephyr/drivers/bluetooth.h"

// MicroPython-specific HCI transport setup function
// This is called by the port to initialize the HCI controller
int bt_hci_transport_setup(const struct device *dev);

#endif /* MP_ZEPHYR_DRIVERS_BLUETOOTH_WRAPPER_H_ */
