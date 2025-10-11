/*
 * Zephyr devicetree.h wrapper for MicroPython
 * Minimal device tree macros (we use CONFIG_ZTEST to bypass most of this)
 */

#ifndef ZEPHYR_DEVICETREE_H_
#define ZEPHYR_DEVICETREE_H_

// Device tree macros for HCI device
// We provide a static HCI device that ports must define

// Forward declare the HCI device (defined by port)
struct device;
extern const struct device mp_bluetooth_zephyr_hci_dev;

// HCI device chosen node macros
#define DT_HAS_CHOSEN(name) DT_HAS_CHOSEN_##name
#define DT_HAS_CHOSEN_zephyr_bt_hci 1

#define DT_CHOSEN(name) DT_CHOSEN_##name
#define DT_CHOSEN_zephyr_bt_hci /* expands to nothing */

// DEVICE_DT_GET returns our static HCI device
#define DEVICE_DT_GET(node) (&mp_bluetooth_zephyr_hci_dev)

// Other device tree macros (not used)
#define DT_NODE_HAS_PROP(node, prop) 0
#define DT_PROP_OR(node, prop, default_value) (default_value)
#define DT_DRV_INST(inst) 0

#endif /* ZEPHYR_DEVICETREE_H_ */
