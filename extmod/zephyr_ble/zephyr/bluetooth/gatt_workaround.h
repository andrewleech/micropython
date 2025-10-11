/*
 * GATT service macro workaround for MicroPython
 * Works around "initializer element is not constant" error with ARRAY_SIZE
 */

#ifndef ZEPHYR_BLUETOOTH_GATT_WORKAROUND_H_
#define ZEPHYR_BLUETOOTH_GATT_WORKAROUND_H_

// Redefine BT_GATT_SERVICE to use compound literal instead of struct initializer
// This avoids the "initializer not constant" error with ARRAY_SIZE
#undef BT_GATT_SERVICE
#define BT_GATT_SERVICE(_attrs)                                 \
{                                                               \
    .attrs = _attrs,                                            \
    .attr_count = (sizeof(_attrs) / sizeof((_attrs)[0])),      \
}

#endif /* ZEPHYR_BLUETOOTH_GATT_WORKAROUND_H_ */
