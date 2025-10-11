/*
 * Zephyr bluetooth/addr.h wrapper for MicroPython
 * Ensures stdbool.h is included before real addr.h
 */

#ifndef ZEPHYR_BLUETOOTH_ADDR_WRAPPER_H_
#define ZEPHYR_BLUETOOTH_ADDR_WRAPPER_H_

#include <stdbool.h>
#include <stdint.h>

// Include the real Zephyr addr.h
#include "../../../../lib/zephyr/include/zephyr/bluetooth/addr.h"

#endif /* ZEPHYR_BLUETOOTH_ADDR_WRAPPER_H_ */
