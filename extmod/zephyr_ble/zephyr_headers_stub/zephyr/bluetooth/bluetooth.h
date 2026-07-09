/*
 * This file is part of the MicroPython project, http://micropython.org/
 *
 * SPDX-License-Identifier: Apache-2.0
 *
 * Wrapper for zephyr/bluetooth/bluetooth.h
 *
 * This wrapper includes the real Zephyr BLE header and adds the bt_str.h
 * utility functions to avoid modifying Zephyr source files.
 */

#ifndef MP_ZEPHYR_BLUETOOTH_WRAPPER_H_
#define MP_ZEPHYR_BLUETOOTH_WRAPPER_H_

// Include the real Zephyr bluetooth.h
// The include path is set up to find lib/zephyr/include
#include_next <zephyr/bluetooth/bluetooth.h>

// Note: bt_addr_le_str and other bt_str.h functions are implemented in bt_str.c
// They will generate "implicit declaration" warnings but will link correctly.
// We cannot include bt_str.h here because it's an internal header that conflicts
// when included from public headers. The warnings are acceptable.

#endif /* MP_ZEPHYR_BLUETOOTH_WRAPPER_H_ */
