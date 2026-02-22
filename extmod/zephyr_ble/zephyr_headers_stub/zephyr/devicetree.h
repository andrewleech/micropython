/*
 * This file is part of the MicroPython project, http://micropython.org/
 *
 * SPDX-License-Identifier: Apache-2.0
 *
 * Minimal devicetree.h stub for Zephyr BLE without full OS.
 *
 * Provides the DT macro API (DT_NODELABEL, DT_NODE_HAS_PROP, DT_PROP, etc.)
 * used by the Zephyr BLE controller. In a real Zephyr build, this is a
 * 4000+ line header; we only need the token-pasting macros that the
 * controller's lll_vendor.h uses.
 */

#ifndef ZEPHYR_INCLUDE_DEVICETREE_H_
#define ZEPHYR_INCLUDE_DEVICETREE_H_

// Pull in the generated token definitions (node labels, property _EXISTS flags)
#include <zephyr/devicetree_generated.h>

// IS_ENABLED comes from sys/util_macro.h → util_internal.h.
// It may already be defined if sys/util.h was included first.
// If not, provide the minimal IS_ENABLED implementation here.
#ifndef IS_ENABLED

// The IS_ENABLED trick: token-paste the macro value with _XXXX.
// If value is 1: _XXXX1 → _YYYY, (two tokens) → selects 1.
// If value is 0: _XXXX0 (one token, undefined) → selects 0.
#ifndef _XXXX1
#define _XXXX1 _YYYY,
#endif

#define Z_IS_ENABLED1(config_macro) Z_IS_ENABLED2(_XXXX##config_macro)
#define Z_IS_ENABLED2(one_or_two_args) Z_IS_ENABLED3(one_or_two_args 1, 0)
#define Z_IS_ENABLED3(ignore_this, val, ...) val
#define IS_ENABLED(config_macro) Z_IS_ENABLED1(config_macro)

#endif // IS_ENABLED

// --- Devicetree macro API ---

// Token concatenation helpers
#ifndef DT_CAT
#define DT_CAT(a, b) a ## b
#endif

#ifndef DT_CAT3
#define DT_CAT3(a, b, c) a ## b ## c
#endif

#ifndef DT_CAT4
#define DT_CAT4(a, b, c, d) a ## b ## c ## d
#endif

// DT_NODELABEL(label) → DT_N_NODELABEL_<label>
// The generated header must define DT_N_NODELABEL_<label> as a node identifier.
#ifndef DT_NODELABEL
#define DT_NODELABEL(label) DT_CAT(DT_N_NODELABEL_, label)
#endif

// DT_NODE_HAS_PROP(node_id, prop) → IS_ENABLED(<node_id>_P_<prop>_EXISTS)
// Returns 1 if the property exists, 0 otherwise.
#ifndef DT_NODE_HAS_PROP
#define DT_NODE_HAS_PROP(node_id, prop) \
    IS_ENABLED(DT_CAT4(node_id, _P_, prop, _EXISTS))
#endif

// DT_PROP(node_id, prop) → <node_id>_P_<prop>
// Returns the property value (must be defined in devicetree_generated.h).
#ifndef DT_PROP
#define DT_PROP(node_id, prop) DT_CAT3(node_id, _P_, prop)
#endif

// DT_CHOSEN(name) → DT_CHOSEN_<name>
#ifndef DT_CHOSEN
#define DT_CHOSEN(name) DT_CAT(DT_CHOSEN_, name)
#endif

// DT_HAS_CHOSEN(name) → IS_ENABLED(DT_CHOSEN_<name>_EXISTS)
#ifndef DT_HAS_CHOSEN
#define DT_HAS_CHOSEN(name) IS_ENABLED(DT_CAT3(DT_CHOSEN_, name, _EXISTS))
#endif

// DT_DEP_ORD(node_id) → <node_id>_ORD
#ifndef DT_DEP_ORD
#define DT_DEP_ORD(node_id) DT_CAT(node_id, _ORD)
#endif

// DT_FOREACH_STATUS_OKAY_NODE(fn) — iterate over all "okay" nodes
// With no real devicetree, this expands to nothing.
#ifndef DT_FOREACH_STATUS_OKAY_NODE
#define DT_FOREACH_STATUS_OKAY_NODE(fn) DT_FOREACH_OKAY_HELPER(fn)
#endif

// DT_INST macros for instance-based access
#ifndef DT_INST
#define DT_INST(inst, compat) DT_CAT3(DT_N_INST_, inst, _ ## compat)
#endif

#endif /* ZEPHYR_INCLUDE_DEVICETREE_H_ */
