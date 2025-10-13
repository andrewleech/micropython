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

// The helper macro that DT_FOREACH_STATUS_OKAY_NODE uses to iterate over nodes.
// With no nodes, this expands to nothing.
#define DT_FOREACH_OKAY_HELPER(fn) /* empty - no devicetree nodes */

// Define a special node identifier for our BT HCI device
// We use DT_N_S_soc_S_bluetooth_hci_0 as the node identifier
// This matches Zephyr's devicetree node naming convention
#define DT_N_S_soc_S_bluetooth_hci_0 DT_N_S_soc_S_bluetooth_hci_0

// Chosen node for zephyr,bt-hci points to our BT HCI node
#define DT_CHOSEN_zephyr_bt_hci DT_N_S_soc_S_bluetooth_hci_0

// Define dependency ordinal macro (required by Zephyr devicetree system)
// This macro is normally generated in devicetree_generated.h
// Need double expansion for token concatenation to work with macro arguments
#define DT_DEP_ORD_HELPER(node_id) node_id ## _ORD
#define DT_DEP_ORD(node_id) DT_DEP_ORD_HELPER(node_id)

// Define dependency ordinal for our BT HCI device node
// Zephyr's DT_DEP_ORD(node_id) expands to DT_CAT(node_id, _ORD)
// So we need DT_N_S_soc_S_bluetooth_hci_0_ORD
#define DT_N_S_soc_S_bluetooth_hci_0_ORD 0

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
