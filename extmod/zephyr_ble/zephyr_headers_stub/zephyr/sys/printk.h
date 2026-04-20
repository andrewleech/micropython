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

// Use standard snprintf
#define snprintk snprintf

#endif /* MP_ZEPHYR_SYS_PRINTK_H_ */
