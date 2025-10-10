/*
 * Zephyr sys/atomic.h wrapper for MicroPython
 * Redirects to our HAL atomic implementation
 */

#ifndef ZEPHYR_SYS_ATOMIC_H_
#define ZEPHYR_SYS_ATOMIC_H_

// Use our HAL atomic implementation
#include "../../hal/zephyr_ble_atomic.h"

#endif /* ZEPHYR_SYS_ATOMIC_H_ */
