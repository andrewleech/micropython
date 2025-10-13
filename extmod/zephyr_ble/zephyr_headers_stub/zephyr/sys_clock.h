/*
 * This file is part of the MicroPython project, http://micropython.org/
 *
 * SPDX-License-Identifier: Apache-2.0
 *
 * Minimal sys_clock.h stub for Zephyr BLE
 *
 * Prevents real sys_clock.h from including sys/clock.h which conflicts
 * with our k_timeout_t definition in zephyr_ble_timer.h
 */

#ifndef MP_ZEPHYR_SYS_CLOCK_WRAPPER_H_
#define MP_ZEPHYR_SYS_CLOCK_WRAPPER_H_

// k_timeout_t is already defined in zephyr_ble_timer.h (via zephyr_ble_hal.h)
// No need to include sys/clock.h

// Prevent real sys_clock.h from being included
#ifndef ZEPHYR_INCLUDE_SYS_CLOCK_H_
#define ZEPHYR_INCLUDE_SYS_CLOCK_H_
#endif

#endif /* MP_ZEPHYR_SYS_CLOCK_WRAPPER_H_ */
