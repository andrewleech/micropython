/*
 * This file is part of the MicroPython project, http://micropython.org/
 *
 * SPDX-License-Identifier: Apache-2.0
 *
 * Stub for zephyr/sys/printk.h
 */

#ifndef MP_ZEPHYR_SYS_PRINTK_H_
#define MP_ZEPHYR_SYS_PRINTK_H_

#include <stdio.h>    // For snprintf
#include <stdbool.h>  // For bool type

// printk stub - no-op by default to avoid polluting output
// Enable ZEPHYR_BLE_PRINTK_DEBUG to route to mp_printf for debugging
#ifndef printk
#if defined(ZEPHYR_BLE_PRINTK_DEBUG) && ZEPHYR_BLE_PRINTK_DEBUG
#include "py/mpprint.h"
#define printk(...) mp_printf(&mp_plat_print, __VA_ARGS__)
#else
#define printk(...) ((void)0)
#endif
#endif

// Use standard snprintf
#define snprintk snprintf

#endif /* MP_ZEPHYR_SYS_PRINTK_H_ */
