/*
 * Zephyr device.h wrapper for MicroPython
 * Minimal device structure for HCI driver
 */

#ifndef ZEPHYR_DEVICE_H_
#define ZEPHYR_DEVICE_H_

#include <stddef.h>

// Minimal device structure (just enough for HCI driver API)
struct device {
    const char *name;
    const void *api;
    void *data;
};

// Device API macros (no-ops in MicroPython)
#define __subsystem

#endif /* ZEPHYR_DEVICE_H_ */
