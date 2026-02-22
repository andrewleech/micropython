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

// DEVICE_API macro — declares a typed API variable name
// In real Zephyr: static DEVICE_API(bt_hci, name) = { .open = ..., .send = ..., ... };
// expands to: static const struct bt_hci_driver_api name = { ... };
#define DEVICE_API(api_type, var_name) const struct api_type##_driver_api var_name

// DEVICE_DT_INST_DEFINE — instantiate a device from devicetree instance
// In real Zephyr this creates a linker-placed device struct. We just create
// a static device struct that sets the API pointer, mirroring __device_dts_ord_0.
// The controller's hci_driver.c uses this to register itself.
#define DEVICE_DT_INST_DEFINE(inst, init_fn_, pm_, data_, config_, level_, prio_, api_, ...) \
    static struct device_state __device_state_##inst = { .init_res = 0, .initialized = true }; \
    const struct device __device_dts_ord_##inst __attribute__((used)) = { \
        .name = "bt_hci_controller", \
        .api = api_, \
        .data = (void *)data_, \
        .config = config_, \
        .state = &__device_state_##inst, \
    };

// Note: BT_HCI_CONTROLLER_INIT is defined in hci_driver.c itself using
// DEVICE_DT_INST_DEFINE — no need for a fallback here.

#ifdef __cplusplus
}
#endif

#endif /* MP_ZEPHYR_DEVICE_H_ */
