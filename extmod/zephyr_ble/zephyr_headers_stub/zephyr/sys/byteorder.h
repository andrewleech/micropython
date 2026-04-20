/*
 * This file is part of the MicroPython project, http://micropython.org/
 *
 * SPDX-License-Identifier: Apache-2.0
 *
 * Wrapper for zephyr/sys/byteorder.h
 *
 * Handles byte-order macro redefinition conflicts between
 * zephyr_ble_config.h and real Zephyr headers.
 */

#ifndef MP_ZEPHYR_SYS_BYTEORDER_WRAPPER_H_
#define MP_ZEPHYR_SYS_BYTEORDER_WRAPPER_H_

// Undef byte-order macros that may have been defined in zephyr_ble_config.h
// Zephyr's byteorder.h will redefine them properly
#undef sys_cpu_to_le16
#undef sys_cpu_to_le32
#undef sys_le16_to_cpu
#undef sys_le32_to_cpu
#undef sys_cpu_to_be16
#undef sys_cpu_to_be32
#undef sys_be16_to_cpu
#undef sys_be32_to_cpu

// Suppress macro redefinition warnings
#pragma GCC diagnostic push
#pragma GCC diagnostic ignored "-Wcpp"

// Include the real byteorder.h
#include_next <zephyr/sys/byteorder.h>

#pragma GCC diagnostic pop

#endif /* MP_ZEPHYR_SYS_BYTEORDER_WRAPPER_H_ */
