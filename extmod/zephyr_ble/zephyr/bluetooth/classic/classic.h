/*
 * Zephyr bluetooth/classic/classic.h wrapper for MicroPython
 * Minimal stubs for Classic Bluetooth (disabled, but needed for compilation)
 */

#ifndef ZEPHYR_BLUETOOTH_CLASSIC_CLASSIC_H_
#define ZEPHYR_BLUETOOTH_CLASSIC_CLASSIC_H_

#include <zephyr/bluetooth/addr.h>

// Minimal struct for bt_br_oob (used in id.c even when Classic BT is disabled)
struct bt_br_oob {
    bt_addr_t addr;
};

#endif /* ZEPHYR_BLUETOOTH_CLASSIC_CLASSIC_H_ */
