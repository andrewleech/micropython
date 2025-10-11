/*
 * Zephyr bluetooth/crypto.h wrapper for MicroPython
 * Ensures addr.h is included before real crypto.h
 */

#ifndef ZEPHYR_BLUETOOTH_CRYPTO_WRAPPER_H_
#define ZEPHYR_BLUETOOTH_CRYPTO_WRAPPER_H_

// Include addr.h first to get bt_addr_t and bt_addr_le_t types
#include <zephyr/bluetooth/addr.h>

// Include the real Zephyr crypto.h
#include "../../../../lib/zephyr/include/zephyr/bluetooth/crypto.h"

#endif /* ZEPHYR_BLUETOOTH_CRYPTO_WRAPPER_H_ */
