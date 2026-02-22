/*
 * This file is part of the MicroPython project, http://micropython.org/
 *
 * SPDX-License-Identifier: Apache-2.0
 *
 * Stub for zephyr/irq.h — blocks the real Zephyr IRQ header and redirects
 * to our IRQ shim (zephyr_ble_irq.h) which provides the same API using
 * NVIC directly.  Only included on controller builds — host-only ports
 * (e.g. RP2) have their own irq_enable/disable/is_enabled via platform SDK.
 */

#ifndef ZEPHYR_INCLUDE_IRQ_H_
#define ZEPHYR_INCLUDE_IRQ_H_

#if MICROPY_BLUETOOTH_ZEPHYR_CONTROLLER
#include "zephyr_ble_irq.h"
#endif

#endif /* ZEPHYR_INCLUDE_IRQ_H_ */
