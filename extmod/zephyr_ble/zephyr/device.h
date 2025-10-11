/*
 * Zephyr device.h wrapper for MicroPython
 * Minimal device structure for HCI driver
 */

#ifndef ZEPHYR_DEVICE_H_
#define ZEPHYR_DEVICE_H_

#include <stddef.h>
#include <stdbool.h>

// Minimal device structure (just enough for HCI driver API)
struct device {
    const char *name;
    const void *api;
    void *data;
};

// Device API macros (no-ops in MicroPython)
#define __subsystem

// Check if device is ready (always returns true in MicroPython)
static inline bool device_is_ready(const struct device *dev) {
    return dev != NULL;
}

#endif /* ZEPHYR_DEVICE_H_ */
