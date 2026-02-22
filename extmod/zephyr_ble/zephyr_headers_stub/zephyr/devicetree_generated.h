/*
 * This file is part of the MicroPython project, http://micropython.org/
 *
 * SPDX-License-Identifier: Apache-2.0
 *
 * Minimal devicetree_generated.h stub for Zephyr BLE without OS
 *
 * In full Zephyr, this file is auto-generated from device tree sources.
 * For MicroPython, device configuration is provided by port-specific code.
 */

#ifndef ZEPHYR_INCLUDE_DEVICETREE_GENERATED_H_
#define ZEPHYR_INCLUDE_DEVICETREE_GENERATED_H_

// In a real Zephyr build, this file contains generated macros for all devicetree nodes.
// For MicroPython, we have no devicetree, so the iteration helper expands to nothing.

// Device tree stubs — provide missing node/property definitions
// The real devicetree.h macros expand to generated token-pasting expressions
// e.g. DT_NODE_HAS_PROP(DT_NODELABEL(hfxo), startup_time_us) expands to:
//   IS_ENABLED(DT_CAT4(DT_N_NODELABEL_hfxo, _P_, startup_time_us, _EXISTS))
// DT_NODELABEL(hfxo) → DT_N_NODELABEL_hfxo (must be defined as a node_id token)
// Then DT_CAT4 produces: <node_id>_P_startup_time_us_EXISTS
// We define the node label and property _EXISTS as 0 so the check fails gracefully.
#define DT_N_NODELABEL_hfxo DT_N_NODELABEL_hfxo
#define DT_N_NODELABEL_hfxo_P_startup_time_us_EXISTS 0

// The helper macro that DT_FOREACH_STATUS_OKAY_NODE uses to iterate over nodes.
// With no nodes, this expands to nothing.
#define DT_FOREACH_OKAY_HELPER(fn) /* empty - no devicetree nodes */

// Define the BT HCI node identifier
// This is used by DT_CHOSEN() and similar macros
// The value must be a valid identifier that can be concatenated with _ORD etc.
// Note: We don't define DT_N_S_soc_S_bluetooth_hci_0 itself because DEVICE_DT_GET
// in device.h bypasses the devicetree system entirely.

// Chosen node for zephyr,bt-hci - value is arbitrary but must be consistent
#define DT_CHOSEN_zephyr_bt_hci mp_zephyr_bt_hci_node

// Indicate that the chosen node exists (required by DT_HAS_CHOSEN macro)
#define DT_CHOSEN_zephyr_bt_hci_EXISTS 1

// DT_DEP_ORD is provided by Zephyr's devicetree/ordinals.h (included later).
// We only need to define the ordinal _value_ for our BT HCI device node.
// Zephyr's DT_DEP_ORD(node_id) expands to DT_CAT(node_id, _ORD)
// So DT_DEP_ORD(mp_zephyr_bt_hci_node) → mp_zephyr_bt_hci_node_ORD → 0
#define mp_zephyr_bt_hci_node_ORD 0

// ARM NVIC devicetree node stub
// The NVIC (Nested Vector Interrupt Controller) configuration is needed by ARM Cortex-M code
// Define the node and its properties based on the target architecture

// Node identifier for ARM NVIC instance 0
// The value doesn't matter, but it must be a valid C identifier suffix
#define DT_N_INST_0_arm_v7m_nvic DT_N_INST_0_arm_v7m_nvic

// NVIC properties - these are architecture-specific
// For Cortex-M4 (STM32WB55): 4 priority bits (16 priority levels)
// For Cortex-M0: 2 priority bits (4 priority levels)
// For Cortex-M3: 3 or 4 priority bits depending on vendor

#if defined(CONFIG_CPU_CORTEX_M4) || defined(CONFIG_CPU_CORTEX_M7) || defined(CONFIG_CPU_CORTEX_M33)
#define DT_N_INST_0_arm_v7m_nvic_P_arm_num_irq_priority_bits 4
#elif defined(CONFIG_CPU_CORTEX_M3)
#define DT_N_INST_0_arm_v7m_nvic_P_arm_num_irq_priority_bits 4
#elif defined(CONFIG_CPU_CORTEX_M0) || defined(CONFIG_CPU_CORTEX_M23)
#define DT_N_INST_0_arm_v7m_nvic_P_arm_num_irq_priority_bits 2
#else
// Default to 4 for unknown Cortex-M variants
#define DT_N_INST_0_arm_v7m_nvic_P_arm_num_irq_priority_bits 4
#endif

#endif /* ZEPHYR_INCLUDE_DEVICETREE_GENERATED_H_ */
