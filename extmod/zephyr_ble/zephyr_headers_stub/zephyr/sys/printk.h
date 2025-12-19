/*
 * This file is part of the MicroPython project, http://micropython.org/
 *
 * SPDX-License-Identifier: Apache-2.0
 *
 * Stub for zephyr/sys/printk.h
 *
 * Provides no-op printk functions for MicroPython integration.
 * We don't include the real Zephyr printk.h because its inline
 * function declarations conflict with our macro definitions.
 */

#ifndef MP_ZEPHYR_SYS_PRINTK_H_
#define MP_ZEPHYR_SYS_PRINTK_H_

#include <stdio.h>    // For snprintf
#include <stdbool.h>  // For bool type

// No-op printk - Zephyr logging is disabled in MicroPython integration
// Guard prevents redefinition warning if already defined in zephyr_ble_config.h
#ifndef printk
#define printk(...) ((void)0)
#endif

// Use standard snprintf
#define snprintk snprintf

#endif /* MP_ZEPHYR_SYS_PRINTK_H_ */
