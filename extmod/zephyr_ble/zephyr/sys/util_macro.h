/*
 * Zephyr sys/util_macro.h wrapper for MicroPython  
 * Advanced utility macros
 */

#ifndef ZEPHYR_SYS_UTIL_MACRO_H_
#define ZEPHYR_SYS_UTIL_MACRO_H_

// BIT macro (needed by assigned_numbers.h enums)
#ifndef BIT
#define BIT(n) (1UL << (n))
#endif

// Note: COND_CODE_1, COND_CODE_0, __COND_CODE, __COND_CODE_0, __COND_CODE_1
// are defined in zephyr_ble_config.h and should not be redefined here.
// This prevents duplicate definitions and ensures consistent behavior.

// IS_ENABLED macro for CONFIG checks
// Maps config_macro=1 to 1, anything else (including undefined) to 0
#define IS_ENABLED(config) __IS_ENABLED1(config)
#define __IS_ENABLED1(x) __IS_ENABLED2(__XXXX_##x)
#define __XXXX_1 __YYYY_,
#define __IS_ENABLED2(one_or_two_args) __IS_ENABLED3(one_or_two_args 1, 0)
#define __IS_ENABLED3(ignore_this, val, ...) val

// Token concatenation macros
// Note: Multiple naming conventions exist because different Zephyr subsystems use different names:
// - CONCAT/__CONCAT: Used by general Zephyr utilities
// - _CONCAT/_DO_CONCAT: Used by Zephyr BLE host and toolchain headers
// - UTIL_CAT: Used by device tree macros (see line 34)
// All are functionally identical (two-level macro for proper token expansion).

// General Zephyr concatenation
#ifndef __CONCAT
#define __CONCAT(a, b) a##b
#endif
#ifndef CONCAT
#define CONCAT(a, b) __CONCAT(a, b)
#endif

// BLE host concatenation (required by BT_L2CAP_FIXED_CHANNEL_DEFINE and similar)
#ifndef _DO_CONCAT
#define _DO_CONCAT(x, y) x##y
#endif
#ifndef _CONCAT
#define _CONCAT(x, y) _DO_CONCAT(x, y)
#endif

// String conversion 
#define STRINGIFY(s) __STRINGIFY(s)
#define __STRINGIFY(s) #s

// Macro utilities
#define EMPTY
#define IDENTITY(x) x

// Defer macro expansion
#define DEFER(...) __VA_ARGS__ EMPTY()

// Get number of arguments
#define NUM_VA_ARGS(...) \
    NUM_VA_ARGS_IMPL(__VA_ARGS__, 63,62,61,60,59,58,57,56,55,54,53,52,51,50,49,48,47,46,45,44,43,42,41,40,39,38,37,36,35,34,33,32,31,30,29,28,27,26,25,24,23,22,21,20,19,18,17,16,15,14,13,12,11,10,9,8,7,6,5,4,3,2,1,0)
#define NUM_VA_ARGS_IMPL(_1,_2,_3,_4,_5,_6,_7,_8,_9,_10,_11,_12,_13,_14,_15,_16,_17,_18,_19,_20,_21,_22,_23,_24,_25,_26,_27,_28,_29,_30,_31,_32,_33,_34,_35,_36,_37,_38,_39,_40,_41,_42,_43,_44,_45,_46,_47,_48,_49,_50,_51,_52,_53,_54,_55,_56,_57,_58,_59,_60,_61,_62,_63,N,...) N

// Utility for compile-time code generation
#define UTIL_CAT(a, b) a##b
#define UTIL_INC(x) UTIL_PRIMITIVE_CAT(__UTIL_INC_, x)
#define __UTIL_INC_0 1
#define __UTIL_INC_1 2
#define __UTIL_INC_2 3

// Expanded utility macros as needed by device tree code
#define DT_STRING_UPPER_TOKEN_BY_IDX(node, prop, idx) 0
#define DT_FOREACH_PROP_ELEM_SEP(node, prop, fn, sep) 0
#define UTIL_PRIMITIVE_CAT(a, b) a##b

// Bit manipulation macros
#ifndef WRITE_BIT
#define WRITE_BIT(var, bit, set) \
    ((var) = (set) ? ((var) | BIT(bit)) : ((var) & ~BIT(bit)))
#endif

#endif /* ZEPHYR_SYS_UTIL_MACRO_H_ */
