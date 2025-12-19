/*
 * This file is part of the MicroPython project, http://micropython.org/
 *
 * SPDX-License-Identifier: Apache-2.0
 *
 * Stub for zephyr/device.h
 *
 * Provides minimal device structure and macros needed for HCI drivers
 * without pulling in the full Zephyr device tree and linker machinery.
 */

#ifndef MP_ZEPHYR_DEVICE_H_
#define MP_ZEPHYR_DEVICE_H_

#include <stdbool.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

// Forward declaration
struct device;

// Minimal device state structure
struct device_state {
    uint8_t init_res;
    bool initialized;
};

// Minimal device operations (not used in our implementation)
struct device_ops {
    int (*init)(const struct device *dev);
};

// Minimal device flags type
typedef uint8_t device_flags_t;

// Minimal device structure for HCI drivers
struct device {
    const char *name;
    const void *config;
    const void *api;
    struct device_state *state;
    void *data;
    struct device_ops ops;
    device_flags_t flags;
};

// Device is always ready in MicroPython (no device tree initialization)
static inline bool device_is_ready(const struct device *dev) {
    (void)dev;
    return true;
}

// __subsystem attribute for driver API structures
#ifndef __subsystem
#define __subsystem
#endif

// Device initialization flags
#define DEVICE_FLAG_INIT_DEFERRED 0

// DEVICE_DT_GET macro - gets device pointer from devicetree node
// In real Zephyr, this expands to &__device_dts_ord_<ordinal> based on node_id.
// In our stub, node_id is ignored - we only have one HCI device.
// The port must define __device_dts_ord_0 (see mpzephyrport.c)
#define DEVICE_DT_GET(node_id) (&__device_dts_ord_0)

// External declaration of the HCI device (defined in port's mpzephyrport.c)
extern const struct device __device_dts_ord_0;

#ifdef __cplusplus
}
#endif

#endif /* MP_ZEPHYR_DEVICE_H_ */
