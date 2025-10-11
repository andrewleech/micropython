/*
 * Zephyr bluetooth/gatt.h wrapper for MicroPython
 * Includes real gatt.h and works around ARRAY_SIZE initializer issue
 */

#ifndef ZEPHYR_BLUETOOTH_GATT_WRAPPER_H_
#define ZEPHYR_BLUETOOTH_GATT_WRAPPER_H_

// Include the real Zephyr gatt.h
#include "../../../../lib/zephyr/include/zephyr/bluetooth/gatt.h"

// Redefine BT_GATT_SERVICE to use explicit cast to make GCC accept it
#undef BT_GATT_SERVICE
#define BT_GATT_SERVICE(_attrs)						\
{									\
	.attrs = _attrs,						\
	.attr_count = (size_t)(sizeof(_attrs) / sizeof((_attrs)[0])),	\
}

#endif /* ZEPHYR_BLUETOOTH_GATT_WRAPPER_H_ */
