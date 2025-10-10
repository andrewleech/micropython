/*
 * Zephyr devicetree.h wrapper for MicroPython
 * Minimal device tree macros (we use CONFIG_ZTEST to bypass most of this)
 */

#ifndef ZEPHYR_DEVICETREE_H_
#define ZEPHYR_DEVICETREE_H_

// Device tree not used in MicroPython integration
// CONFIG_ZTEST in autoconf.h causes BLE host to skip device tree checks

// Basic device tree macros (return values indicating "not present")
#define DT_HAS_CHOSEN(name) 0
#define DT_CHOSEN(name) 0
#define DT_NODE_HAS_PROP(node, prop) 0
#define DT_PROP_OR(node, prop, default_value) (default_value)

// Device instantiation macros (not used, but defined for completeness)
#define DT_DRV_INST(inst) 0
#define DEVICE_DT_GET(node) NULL

#endif /* ZEPHYR_DEVICETREE_H_ */
