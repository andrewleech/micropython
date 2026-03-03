/*
 * Zephyr BLE Settings — bond key storage via Python secret callbacks.
 */

#ifndef MICROPY_INCLUDED_EXTMOD_ZEPHYR_BLE_HAL_SETTINGS_H
#define MICROPY_INCLUDED_EXTMOD_ZEPHYR_BLE_HAL_SETTINGS_H

// Zephyr-specific secret type codes passed to _IRQ_GET_SECRET / _IRQ_SET_SECRET.
// Type values are stack-specific (see modbluetooth.h) — these overlap with NimBLE's
// type codes but the two stacks are never mixed in the same build.
#define MP_BLUETOOTH_ZEPHYR_SECRET_KEYS  1  // Pairing/bonding keys

#if MICROPY_PY_BLUETOOTH_ENABLE_PAIRING_BONDING
// Load stored bond keys from Python secret callbacks into Zephyr's key_pool[].
// Called from mp_bluetooth_init() after bt_enable() succeeds.
void mp_bluetooth_zephyr_load_keys(void);
#endif

#endif // MICROPY_INCLUDED_EXTMOD_ZEPHYR_BLE_HAL_SETTINGS_H
