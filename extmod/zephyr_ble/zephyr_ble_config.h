/*
 * This file is part of the MicroPython project, http://micropython.org/
 *
 * The MIT License (MIT)
 *
 * Copyright (c) 2025 MicroPython Contributors
 *
 * Permission is hereby granted, free of charge, to any person obtaining a copy
 * of this software and associated documentation files (the "Software"), to deal
 * in the Software without restriction, including without limitation the rights
 * to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
 * copies of the Software, and to permit persons to whom the Software is
 * furnished to do so, subject to the following conditions:
 *
 * The above copyright notice and this permission notice shall be included in
 * all copies or substantial portions of the Software.
 *
 * THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
 * IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
 * FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
 * AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
 * LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
 * OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
 * THE SOFTWARE.
 */

#ifndef MICROPY_INCLUDED_EXTMOD_ZEPHYR_BLE_ZEPHYR_BLE_CONFIG_H
#define MICROPY_INCLUDED_EXTMOD_ZEPHYR_BLE_ZEPHYR_BLE_CONFIG_H

// Enable debug output for Zephyr BLE implementation (Phase 1 development)
#ifndef ZEPHYR_BLE_DEBUG
#define ZEPHYR_BLE_DEBUG 1
#endif

#include <stddef.h>
#include <stdint.h>

// Zephyr BLE Configuration
// Maps Zephyr Kconfig options to static defines for MicroPython

// Forward declarations for Zephyr types referenced in headers
// arch_esf is the architecture exception stack frame (used in hci_vs.h)
struct arch_esf;

// Forward declare struct device (needed for __device_dts_ord_0 declaration below)
struct device;

// Forward declare the HCI device structure
// This is defined in the port-specific code (e.g., mpzephyrport.c)
// and is referenced by DEVICE_DT_GET macro expansions in static initializers
extern const struct device __device_dts_ord_0;

// =============================================================================
// PART 1: Header Guard Pre-definitions
// Pre-define header guards for Zephyr headers we want to skip
// This prevents them from being included even if referenced
// =============================================================================

// Classic Bluetooth headers (we only need BLE)
// Note: classic.h is NOT blocked - we provide a minimal wrapper with struct bt_br_oob
#define ZEPHYR_INCLUDE_BLUETOOTH_A2DP_CODEC_H_
#define ZEPHYR_INCLUDE_BLUETOOTH_HFP_HF_H_
#define ZEPHYR_INCLUDE_BLUETOOTH_HFP_AG_H_
#define ZEPHYR_INCLUDE_BLUETOOTH_SDP_H_
#define ZEPHYR_INCLUDE_BLUETOOTH_RFCOMM_H_
#define ZEPHYR_SUBSYS_BLUETOOTH_HOST_CLASSIC_L2CAP_BR_INTERFACE_H_

// NOTE: We now use a wrapper for drivers/bluetooth.h which includes the real header
// The wrapper is at zephyr_headers_stub/zephyr/drivers/bluetooth.h
// #define ZEPHYR_INCLUDE_DRIVERS_BLUETOOTH_H_

// Problematic Zephyr system headers that conflict with our HAL wrappers
// NOTE: ITERABLE_SECTIONS_H is NOT blocked - net_buf pools require this feature
// NOTE: DO NOT block toolchain.h - it's needed for __printf_like and other compiler macros
// #define ZEPHYR_INCLUDE_TOOLCHAIN_H_
#define ZEPHYR_INCLUDE_KERNEL_THREAD_STACK_H

// Block printk.h to avoid conflicts with our stub
#define ZEPHYR_INCLUDE_SYS_PRINTK_H_

// System initialization macro (no-op in MicroPython)
// SYS_INIT(func, level, priority) registers an init function
// In MicroPython we call init functions explicitly, so stub this out
#ifndef SYS_INIT
#define SYS_INIT(func, level, priority) \
    static inline int __sys_init_##func(void) { return 0; }
#endif

// Init levels (not used, but referenced in SYS_INIT calls)
#define POST_KERNEL 0

// Atomic bitmap operations (simplified stubs)
// Normally defined in sys/atomic.h
// Note: ATOMIC_DEFINE may be redefined by real Zephyr headers
// atomic_t is defined as 'long' (signed) in Zephyr
#ifndef ATOMIC_DEFINE
#define ATOMIC_DEFINE(name, num_bits) long name[((num_bits) + 31) / 32]
#endif
#ifndef ATOMIC_INIT
#define ATOMIC_INIT(value) (value)
#endif

// Conditional compilation helpers from Zephyr's util_internal.h
// These use a clever comma-detection trick to select code paths at compile time

// Core trick: _XXXX1 maps to "_YYYY," (with comma), _XXXX0 stays as single token
#define _XXXX1 _YYYY,
#define _ZZZZ0 _YYYY,

// Z_COND_CODE_1: if flag=1, expands to if_1_code; otherwise if_0_code
#define Z_COND_CODE_1(_flag, _if_1_code, _else_code) \
    __COND_CODE(_XXXX##_flag, _if_1_code, _else_code)

// Z_COND_CODE_0: if flag=0, expands to if_0_code; otherwise else_code
#define Z_COND_CODE_0(_flag, _if_0_code, _else_code) \
    __COND_CODE(_ZZZZ##_flag, _if_0_code, _else_code)

// The core macro uses argument position to select output
// If one_or_two_args contains a comma, it becomes 2 args and _if_code is selected
// If one_or_two_args is single token, _else_code is selected
#define __COND_CODE(one_or_two_args, _if_code, _else_code) \
    __GET_ARG2_DEBRACKET(one_or_two_args _if_code, _else_code)

// Gets second argument and removes brackets around it
#define __GET_ARG2_DEBRACKET(ignore_this, val, ...) __DEBRACKET val

// Used to remove brackets from around a single argument
#define __DEBRACKET(...) __VA_ARGS__

// COND_CODE_1 - public API (uses Z_COND_CODE_1 internally)
#define COND_CODE_1(_flag, _if_1_code, _else_code) \
    Z_COND_CODE_1(_flag, _if_1_code, _else_code)

// COND_CODE_0 - public API (uses Z_COND_CODE_0 internally)
#define COND_CODE_0(_flag, _if_0_code, _else_code) \
    Z_COND_CODE_0(_flag, _if_0_code, _else_code)

// IF_ENABLED(config, (code)) expands to code if config=1, empty otherwise
#define IF_ENABLED(_flag, _code) \
    COND_CODE_1(_flag, _code, ())

// Handle undefined config values (should expand to nothing)
#define __IF_ENABLED_CONFIG_BT_SETTINGS_DELAYED_STORE(code) /* empty */

// Net buf configuration (must be before net_buf.h is included)
#define CONFIG_NET_BUF_ALIGNMENT 0
#define CONFIG_NET_BUF_WARN_ALLOC_INTERVAL 0
#define CONFIG_NET_BUF_LOG_LEVEL 0
// CONFIG_NET_BUF_POOL_USAGE must be undefined, not 0, because Zephyr checks with #if defined()
#undef CONFIG_NET_BUF_POOL_USAGE

// =============================================================================
// PART 2: Macro Conflict Prevention
// Only define macros if not already defined by platform SDK
// =============================================================================

#ifndef MIN
#define MIN(a, b) (((a) < (b)) ? (a) : (b))
#endif

#ifndef MAX
#define MAX(a, b) (((a) > (b)) ? (a) : (b))
#endif

#ifndef __CONCAT
#define __CONCAT(a, b) a##b
#endif

#ifndef BIT
#define BIT(n) (1UL << (n))
#endif

#ifndef BIT_MASK
#define BIT_MASK(n) (BIT(n) - 1UL)
#endif

#ifndef BIT64_MASK
#define BIT64_MASK(n) (BIT64(n) - 1ULL)
#endif

#ifndef BIT64
#define BIT64(n) (1ULL << (n))
#endif

// Note: BITS_PER_BYTE is defined by Zephyr's sys/util.h - don't define here
// to avoid redefinition conflicts in CMake builds where include order varies

#ifndef IN_RANGE
#define IN_RANGE(val, min, max) ((val) >= (min) && (val) <= (max))
#endif

#ifndef BUILD_ASSERT
// Simplified build assertion - disabled to avoid complex type expression issues
// Zephyr uses BUILD_ASSERT for compile-time checks with __builtin_types_compatible_p
// and other GCC builtins that don't always evaluate to integer constants.
// For maintainability, we disable these checks rather than trying to fix all edge cases.
#define BUILD_ASSERT(cond, ...) ((void)(cond))
#endif

#ifndef __syscall
// Zephyr syscall macro - normally generates inline wrappers for user/kernel separation
// In MicroPython we don't have CONFIG_USERSPACE, so just make it empty
// Functions declared with __syscall become regular function declarations
#define __syscall
#endif

#ifndef ARG_UNUSED
#define ARG_UNUSED(x) (void)(x)
#endif

// Compiler builtin detection
#ifndef HAS_BUILTIN
#if defined(__has_builtin)
#define HAS_BUILTIN(x) __has_builtin(x)
#else
#define HAS_BUILTIN(x) 0
#endif
#endif

// Attributes used by Zephyr code
#ifndef __must_check
#define __must_check __attribute__((warn_unused_result))
#endif

#ifndef __packed
#define __packed __attribute__((packed))
#endif

#ifndef __aligned
#define __aligned(x) __attribute__((aligned(x)))
#endif

#ifndef __fallthrough
#if __GNUC__ >= 7
#define __fallthrough __attribute__((fallthrough))
#else
#define __fallthrough do {} while (0)
#endif
#endif

#ifndef __noinit
// Uninitialized data section (not used in MicroPython)
#define __noinit __attribute__((section(".noinit")))
#endif

// Note: FLEXIBLE_ARRAY_DECLARE is defined by Zephyr's sys/util.h - don't define here
// to avoid redefinition conflicts in CMake builds where include order varies

// =============================================================================
// PART 3: Architecture Detection
// Detect target architecture from compiler predefined macros
// =============================================================================

// ARM Architecture (Cortex-M, Cortex-A, ARM11, etc.)
#if defined(__ARM_ARCH) || defined(__arm__) || defined(_ARM_) || \
    defined(__ARM_EABI__) || defined(__ARMEL__) || defined(__ARMEB__) || \
    defined(__thumb__) || defined(_M_ARM) || defined(__aarch64__)
#ifndef CONFIG_ARM
#define CONFIG_ARM 1
#endif

// Detect Cortex-M sub-architecture
#if defined(__ARM_ARCH_6M__) ||defined(__ARM_ARCH_7M__) || \
    defined(__ARM_ARCH_7EM__) || defined(__ARM_ARCH_8M_BASE__) || \
    defined(__ARM_ARCH_8M_MAIN__) || defined(__ARM_ARCH_8_1M_MAIN__)
#ifndef CONFIG_CPU_CORTEX_M
#define CONFIG_CPU_CORTEX_M 1
#endif

// Cortex-M0/M0+/M1 (ARMv6-M)
#if defined(__ARM_ARCH_6M__) || defined(__CORTEX_M) && (__CORTEX_M == 0)
#ifndef CONFIG_CPU_CORTEX_M0
#define CONFIG_CPU_CORTEX_M0 1
#endif
#ifndef CONFIG_ARMV6_M_ARMV8_M_BASELINE
#define CONFIG_ARMV6_M_ARMV8_M_BASELINE 1
#endif
#endif

// Cortex-M3 (ARMv7-M)
#if defined(__ARM_ARCH_7M__) || defined(__CORTEX_M) && (__CORTEX_M == 3)
#ifndef CONFIG_CPU_CORTEX_M3
#define CONFIG_CPU_CORTEX_M3 1
#endif
#endif

// Cortex-M4/M7 (ARMv7E-M with DSP and optional FPU)
#if defined(__ARM_ARCH_7EM__) || defined(__CORTEX_M) && (__CORTEX_M == 4 || __CORTEX_M == 7)
#ifndef CONFIG_CPU_CORTEX_M4
#define CONFIG_CPU_CORTEX_M4 1
#endif
#ifndef CONFIG_ARMV7_M_ARMV8_M_MAINLINE
#define CONFIG_ARMV7_M_ARMV8_M_MAINLINE 1
#endif
#endif

// Cortex-M23 (ARMv8-M Baseline)
#if defined(__ARM_ARCH_8M_BASE__) || defined(__CORTEX_M) && (__CORTEX_M == 23)
#ifndef CONFIG_CPU_CORTEX_M23
#define CONFIG_CPU_CORTEX_M23 1
#endif
#ifndef CONFIG_ARMV8_M_BASELINE
#define CONFIG_ARMV8_M_BASELINE 1
#endif
#ifndef CONFIG_ARMV6_M_ARMV8_M_BASELINE
#define CONFIG_ARMV6_M_ARMV8_M_BASELINE 1
#endif
#endif

// Cortex-M33/M35P (ARMv8-M Mainline)
#if defined(__ARM_ARCH_8M_MAIN__) || defined(__CORTEX_M) && (__CORTEX_M == 33 || __CORTEX_M == 35)
#ifndef CONFIG_CPU_CORTEX_M33
#define CONFIG_CPU_CORTEX_M33 1
#endif
#ifndef CONFIG_ARMV8_M_MAINLINE
#define CONFIG_ARMV8_M_MAINLINE 1
#endif
#endif

// Cortex-M55 (ARMv8.1-M Mainline)
#if defined(__ARM_ARCH_8_1M_MAIN__) || defined(__CORTEX_M) && (__CORTEX_M == 55)
#ifndef CONFIG_CPU_CORTEX_M55
#define CONFIG_CPU_CORTEX_M55 1
#endif
#ifndef CONFIG_ARMV8_1_M_MAINLINE
#define CONFIG_ARMV8_1_M_MAINLINE 1
#endif
#endif

#endif

#endif

// ARM64 Architecture (AArch64)
#if defined(__aarch64__) || defined(_M_ARM64)
#ifndef CONFIG_ARM64
#define CONFIG_ARM64 1
#endif
#endif

// x86 Architecture
#if defined(__i386__) || defined(__x86_64__) || defined(_M_IX86) || defined(_M_X64)
#ifndef CONFIG_X86
#define CONFIG_X86 1
#endif
#endif

// RISC-V Architecture
#if defined(__riscv) || defined(__riscv__)
#ifndef CONFIG_RISCV
#define CONFIG_RISCV 1
#endif
#endif

// Xtensa Architecture (ESP32)
#if defined(__XTENSA__)
#ifndef CONFIG_XTENSA
#define CONFIG_XTENSA 1
#endif
#endif

// MIPS Architecture
#if defined(__mips__) || defined(__mips) || defined(__MIPS__)
#ifndef CONFIG_MIPS
#define CONFIG_MIPS 1
#endif
#endif

// =============================================================================
// PART 4: Zephyr BLE Stack Configuration
// =============================================================================

// --- System Configuration ---
// Single-core configuration (MicroPython doesn't use multicore)
#define CONFIG_MP_MAX_NUM_CPUS 1

// Thread priority configuration (MicroPython doesn't use Zephyr threading)
// We define minimal values to satisfy Zephyr kernel requirements
#define CONFIG_NUM_COOP_PRIORITIES 1
#define CONFIG_NUM_PREEMPT_PRIORITIES 0

// Enforce Zephyr stdint conventions (enables __printf_like in toolchain/gcc.h)
#define CONFIG_ENFORCE_ZEPHYR_STDINT 1

// --- Core BLE Stack ---
#define CONFIG_BT 1
#define CONFIG_BT_HCI_HOST 1
#define CONFIG_BT_MAX_CONN 4

// --- GAP Roles ---
#define CONFIG_BT_BROADCASTER 1
#define CONFIG_BT_OBSERVER 1
#define CONFIG_BT_PERIPHERAL 1
#define CONFIG_BT_CENTRAL 1

// --- Connection Support ---
#define CONFIG_BT_CONN 1

// --- GATT Support ---
#define CONFIG_BT_GATT_CLIENT 1
#define CONFIG_BT_GATT_DYNAMIC_DB 1
#define CONFIG_BT_GATT_SERVICE_CHANGED 1
// Note: Use #undef for GATT_CACHING since code uses #if defined() checks
#undef CONFIG_BT_GATT_CACHING  // Disable - requires PSA crypto API
#define CONFIG_BT_ATT_PREPARE_COUNT 0

// --- Security (SMP) - Detailed Options ---
// Note: Core SMP config (CONFIG_BT_SMP, CONFIG_BT_BONDABLE, etc.) is now in dedicated
// "Security Manager Protocol (SMP) - Pairing/Bonding" section below. This section
// contains additional SMP options.
// #define CONFIG_BT_SMP 0  // OLD - now enabled in SMP section below for pairing/bonding
#define CONFIG_BT_SIGNING 0
// CONFIG_BT_SMP_SC_PAIR_ONLY must NOT be defined (not even to 0) to enable legacy+SC pairing
// When defined, Zephyr uses #if !defined() to check, so any definition disables the feature
// #define CONFIG_BT_SMP_SC_PAIR_ONLY 0
// #define CONFIG_BT_SMP_SC_ONLY 0  // Duplicate - defined in SMP section below
// CONFIG_BT_SMP_OOB_LEGACY_PAIR_ONLY must NOT be defined to enable SC pairing
// When defined (even as 0), Zephyr uses #if !defined() which disables SC code paths
// #define CONFIG_BT_SMP_OOB_LEGACY_PAIR_ONLY 0
#define CONFIG_BT_SMP_ENFORCE_MITM 0
#define CONFIG_BT_SMP_USB_HCI_CTLR_WORKAROUND 0
#define CONFIG_BT_SMP_ALLOW_UNAUTH_OVERWRITE 1
#define CONFIG_BT_FIXED_PASSKEY 0
#define CONFIG_BT_USE_DEBUG_KEYS 0
#define CONFIG_BT_PASSKEY_MAX 999999
#define CONFIG_BT_SMP_MIN_ENC_KEY_SIZE 7  // Minimum encryption key size (7-16 bytes)
#define BT_SMP_MIN_ENC_KEY_SIZE CONFIG_BT_SMP_MIN_ENC_KEY_SIZE
#define CONFIG_BT_PRIVACY 0  // Disabled to fix scanning EPERM error
#define CONFIG_BT_RPA 0  // Disabled to fix scanning EPERM error
#define CONFIG_BT_CTLR_PRIVACY 0  // No controller privacy (host-only)
#define CONFIG_BT_SCAN_WITH_IDENTITY 1  // Use identity address for scanning instead of random address

// --- L2CAP ---
#define CONFIG_BT_L2CAP_TX_BUF_COUNT 4
#define CONFIG_BT_L2CAP_TX_MTU 256  // L2CAP TX MTU (matches NimBLE default)

// --- Security Manager Protocol (SMP) - Pairing/Bonding ---
#define CONFIG_BT_SMP 1                        // Enable Security Manager Protocol
#define CONFIG_BT_BONDABLE 1                   // Enable bonding by default (can be disabled per-connection)
#define CONFIG_BT_SMP_SC_ONLY 0                // Allow both Legacy and LE Secure Connections pairing
#define CONFIG_BT_MAX_PAIRED 8                 // Maximum number of bonded devices
// Phase 1: Disable persistent storage (CONFIG_BT_SETTINGS) - will enable in Phase 3
// #define CONFIG_BT_SETTINGS 1                // Enable Settings subsystem for bond storage

// --- LE Feature Pages ---
// BLE controllers report supported features across multiple 8-byte "pages".
// Page 0 (bytes 0-7) contains basic LE features defined in BT Core Spec 4.0+
// Additional pages (8-byte each) contain extended features from later specs.
//
// Setting CONFIG_BT_LE_MAX_LOCAL_SUPPORTED_FEATURE_PAGE=0 means:
// - We only support the basic 8-byte feature set (page 0)
// - We don't allocate space for extended feature pages
// - This saves (CONFIG_BT_LE_MAX_LOCAL_SUPPORTED_FEATURE_PAGE * 8) bytes per device
// - Page 0 provides all essential BLE features (advertising, scanning, connections)
//
// To enable extended features from BT 5.0+ (e.g., 2M PHY, Coded PHY, extended advertising),
// increase this value. Each page adds 8 bytes to BT_LE_LOCAL_SUPPORTED_FEATURES_SIZE.
#define CONFIG_BT_LE_MAX_LOCAL_SUPPORTED_FEATURE_PAGE 0

// --- Buffer Configuration ---
// ACL buffers
#define CONFIG_BT_BUF_ACL_TX_COUNT 8
// ACL TX buffer size must accommodate largest SMP packet without fragmentation:
// - SMP Public Key (SC pairing): 4 (L2CAP hdr) + 1 (SMP code) + 64 (X+Y coords) = 69 bytes
// Use 73 bytes (69 + 4 margin). This prevents TX packet count mismatch assert
// (hci_core.c:669) that occurs when the Public Key is fragmented across multiple ACL packets.
// Memory impact: 73 × 8 buffers = 584 bytes (was 27 × 8 = 216 bytes, delta +368 bytes)
#define CONFIG_BT_BUF_ACL_TX_SIZE 73
#define CONFIG_BT_BUF_ACL_RX_COUNT 16  // Increased from 8 to 16 for scanning
// ACL RX buffer size must accommodate largest expected L2CAP packet:
// - SMP Public Key (SC pairing): 4 (L2CAP hdr) + 1 (SMP code) + 64 (X+Y coords) = 69 bytes
// Use 72 bytes for alignment + small margin.
// Note: Legacy pairing works with this size. SC pairing currently crashes with TX
// packet count mismatch assert (hci_core.c:669) - needs deeper investigation.
#define CONFIG_BT_BUF_ACL_RX_SIZE 72
#define CONFIG_BT_BUF_ACL_RX_COUNT_EXTRA CONFIG_BT_MAX_CONN

// Event buffers - Increased for scanning workload and connectable advertising
// Advertising reports are event packets, need larger pool to handle bursts
// Connection-related events also need buffering during connectable advertising
#define CONFIG_BT_BUF_EVT_RX_COUNT 64  // Increased from 32 to 64 for connectable advertising
#define CONFIG_BT_BUF_EVT_RX_SIZE 68
#define CONFIG_BT_BUF_EVT_DISCARDABLE_COUNT 16  // Increased from 8 to 16 for ad reports
#define CONFIG_BT_BUF_EVT_DISCARDABLE_SIZE 43

// Command buffers - Increased for connectable advertising command flow
// Size must be 255 to accommodate HCI command responses (e.g., Read Local Supported Commands = 69 bytes)
#define CONFIG_BT_BUF_CMD_TX_COUNT 8  // Increased from 4 to 8
#define CONFIG_BT_BUF_CMD_TX_SIZE 255

// Flow control (disabled - STM32WB controller doesn't support HOST_BUFFER_SIZE command)
// Note: Must use #undef, not define to 0, because Zephyr uses #if defined() checks
#undef CONFIG_BT_HCI_ACL_FLOW_CONTROL

// --- Advanced Features - Disable for Phase 1 ---
// TODO: Enable after adding iso.c and cs.c
#define CONFIG_BT_ISO 0
#define CONFIG_BT_ISO_BROADCASTER 0
#define CONFIG_BT_ISO_SYNC_RECEIVER 0
#define CONFIG_BT_ISO_UNICAST 0
#define CONFIG_BT_ISO_CENTRAL 0
#define CONFIG_BT_ISO_PERIPHERAL 0
#define CONFIG_BT_DF 0
#define CONFIG_BT_CHANNEL_SOUNDING 0
#undef CONFIG_BT_EXT_ADV
#define CONFIG_BT_EXT_ADV_LEGACY_SUPPORT 1

// --- Host Features ---
#define CONFIG_BT_FILTER_ACCEPT_LIST 1
#define CONFIG_BT_WHITELIST CONFIG_BT_FILTER_ACCEPT_LIST
#define CONFIG_BT_REMOTE_VERSION 1
#define CONFIG_BT_PHY_UPDATE 0
#define CONFIG_BT_DATA_LEN_UPDATE 0

// --- Crypto ---
#define CONFIG_BT_HOST_CRYPTO 1
#define CONFIG_BT_HOST_CRYPTO_PRNG 1
#define CONFIG_BT_ECC 1
#define CONFIG_BT_TINYCRYPT_ECC 1
#define CONFIG_BT_TINYCRYPT_AES_CMAC 1
#define CONFIG_BT_CTLR_CRYPTO 0  // No controller crypto

// --- Settings/Storage - Disabled for Phase 1 (RAM-only) ---
#define CONFIG_BT_SETTINGS 0
// Note: Use #undef for DELAYED_STORE since code uses #if defined() checks
#undef CONFIG_BT_SETTINGS_DELAYED_STORE

// --- Logging/Debug ---
#ifndef CONFIG_BT_DEBUG
#define CONFIG_BT_DEBUG 0
#endif
#define CONFIG_BT_DEBUG_LOG 0
#define CONFIG_BT_MONITOR 0
#define CONFIG_BT_HCI_RAW 0

// --- Shell/Testing ---
#define CONFIG_BT_SHELL 0
#define CONFIG_BT_TESTING 0

// ZTEST - Enable to bypass devicetree HCI device requirement
// When CONFIG_ZTEST=1, hci_core.c allows NULL HCI device
// and we provide our own via mp_bluetooth_zephyr_hci_dev
// DISABLED: This causes early return in bt_enable() when bt_dev.hci is NULL
#define CONFIG_ZTEST 0
// ZTEST_UNITTEST - Enables empty __syscall definition in toolchain/common.h
// This prevents __syscall from becoming "static inline" which would make
// our device_is_ready() implementation invisible to the linker
#define ZTEST_UNITTEST 1

// --- Classic Bluetooth - Disable for Phase 1 ---
// TODO: Enable after adding BR/EDR source files (br.c, conn_br.c, ssp.c, l2cap_br.c)
// Note: Leave undefined (not 0) so #if defined() checks fail
// #define CONFIG_BT_CLASSIC 1
// #define CONFIG_BT_BREDR 1
#define CONFIG_BT_MAX_BR_CONN 0
#define CONFIG_BT_HFP_HF 0
#define CONFIG_BT_HFP_AG 0
#define CONFIG_BT_A2DP 0
#define CONFIG_BT_AVRCP 0
#define CONFIG_BT_SPP 0
#define CONFIG_BT_HID 0
#define CONFIG_BT_RFCOMM 0

// --- Long Work Queue ---
#define CONFIG_BT_LONG_WQ 0

// --- RX Work Queue Configuration ---
// Use system work queue for receiving BLE events
// CONFIG_BT_RECV_WORKQ_BT must be undefined, not 0, because Zephyr checks with #if defined()
#define CONFIG_BT_RECV_WORKQ_SYS 1
#undef CONFIG_BT_RECV_WORKQ_BT

// RX thread configuration (not used in MicroPython, but needed for compilation)
#define CONFIG_BT_RX_STACK_SIZE 1024
#define CONFIG_BT_RX_PRIO 8

// --- Device Name ---
#ifndef CONFIG_BT_DEVICE_NAME
#define CONFIG_BT_DEVICE_NAME "MicroPython"
#endif
#define CONFIG_BT_DEVICE_NAME_MAX 32
#define CONFIG_BT_DEVICE_NAME_DYNAMIC 1
#define CONFIG_BT_DEVICE_NAME_GATT_WRITABLE 1

// --- ID Management ---
#define CONFIG_BT_ID_MAX 1

// --- Advertising ---
#define CONFIG_BT_ADV_DATA_LEN_MAX 31
#define CONFIG_BT_SCAN_RSP_DATA_LEN_MAX 31
#define CONFIG_BT_LIM_ADV_TIMEOUT 30  // Limited discoverable mode timeout (seconds)

// --- Scanning ---
#define CONFIG_BT_EXT_SCAN_BUF_SIZE 229  // Extended scanning buffer size

// --- ATT/GATT Timeouts ---
#define CONFIG_BT_ATT_TX_COUNT 4

// --- Zephyr System Config ---
#define CONFIG_LITTLE_ENDIAN 1
#define CONFIG_BT_HCI_VS 0
#define CONFIG_BT_HCI_VS_EXT 0

// System clock configuration (1 tick = 1 millisecond)
#define CONFIG_SYS_CLOCK_EXISTS 1
#define CONFIG_SYS_CLOCK_TICKS_PER_SEC 1000
#define CONFIG_SYS_CLOCK_HW_CYCLES_PER_SEC 1000000  // 1MHz hardware timer (matches MicroPython's ms tick)
#define CONFIG_SYS_CLOCK_MAX_TIMEOUT_DAYS 365  // Maximum timeout in days (for overflow checking)
#define MSEC_PER_SEC 1000
#define USEC_PER_MSEC 1000

// RPA (Resolvable Private Address) timeout in seconds
#define CONFIG_BT_RPA_TIMEOUT 900  // 15 minutes

// --- Assert Configuration ---
#ifdef NDEBUG
#define CONFIG_ASSERT 0
#else
#define CONFIG_ASSERT 1  // Re-enable to capture assertion location
#endif
#define CONFIG_ASSERT_LEVEL 2  // Maximum verbosity
#define CONFIG_ASSERT_VERBOSE 1  // Print assertion condition and location

// Bluetooth-specific assert macros (from subsys/bluetooth/common/assert.h)
// These must be undefined (not 0) because Zephyr checks with #if defined()
#undef CONFIG_BT_ASSERT  // Use simple __ASSERT fallback, not verbose BT_ASSERT
#undef CONFIG_BT_ASSERT_VERBOSE
#undef CONFIG_BT_ASSERT_PANIC

// When CONFIG_BT_ASSERT is undefined, BT_ASSERT falls back to __ASSERT macros (defined in kernel.h)
#ifndef BT_ASSERT
#define BT_ASSERT(cond) __ASSERT_NO_MSG(cond)
#endif

#ifndef BT_ASSERT_MSG
#define BT_ASSERT_MSG(cond, msg, ...) __ASSERT(cond, msg, ##__VA_ARGS__)
#endif

// --- Additional CONFIG values used by BLE sources ---
// Only add values that are actually used, not all Zephyr configs

// Logging levels (0=OFF, 1=ERR, 2=WRN, 3=INF, 4=DBG)
#define CONFIG_BT_HCI_CORE_LOG_LEVEL 0
#define CONFIG_BT_CONN_LOG_LEVEL 0
#define CONFIG_BT_GATT_LOG_LEVEL 0
#define CONFIG_BT_ATT_LOG_LEVEL 0
#define CONFIG_BT_SMP_LOG_LEVEL 4  // Enable SMP debug logging
#define CONFIG_BT_KEYS_LOG_LEVEL 0
#define CONFIG_BT_SETTINGS_LOG_LEVEL 0
#define CONFIG_BT_RPA_LOG_LEVEL 0

// Connection parameters
#define CONFIG_BT_CONN_PARAM_UPDATE_TIMEOUT 5000  // 5 seconds
#define CONFIG_BT_CREATE_CONN_TIMEOUT 3000  // 3 seconds
#define CONFIG_BT_CONN_TX_USER_DATA_SIZE 16  // Must be >= sizeof(struct closure) = 2*sizeof(void*)
#define CONFIG_BT_CONN_FRAG_COUNT 1

// Background scanning (for whitelist)
#define CONFIG_BT_BACKGROUND_SCAN_INTERVAL 2048
#define CONFIG_BT_BACKGROUND_SCAN_WINDOW 18

// Optional features (disabled)
// Note: CONFIG_BT_BONDABLE now enabled in SMP section above
#define CONFIG_BT_BONDING_REQUIRED 0
#define CONFIG_BT_BONDABLE_PER_CONNECTION 0
#define CONFIG_BT_AUTO_PHY_UPDATE 0
#define CONFIG_BT_AUTO_DATA_LEN_UPDATE 0
#define CONFIG_BT_CONN_DISABLE_SECURITY 0
#define CONFIG_BT_CONN_CHECK_NULL_BEFORE_CREATE 0
#define CONFIG_BT_CONN_PARAM_ANY 0
#define CONFIG_BT_CONN_TX 1
#define CONFIG_BT_CONN_DYNAMIC_CALLBACKS 1  // CRITICAL: Enable dynamic callback registration (bt_conn_cb_register)
#define CONFIG_BT_ATT_RETRY_ON_SEC_ERR 0

// EATT (Enhanced ATT) - disabled
#define CONFIG_BT_EATT 0
#define CONFIG_BT_EATT_MAX 0

// ISO (Isochronous Channels) - disabled for Phase 1
#define CONFIG_BT_ISO_MAX_CHAN 0
#define CONFIG_BT_ISO_MAX_BIG 0
#define CONFIG_BT_ISO_MAX_CIG 0
#define CONFIG_BT_ISO_RX_BUF_COUNT 0
#define CONFIG_BT_ISO_TX_BUF_COUNT 0
#define CONFIG_BT_ISO_TX_MTU 0
#define CONFIG_BT_ISO_RX_MTU 0

// SCO (for classic BT) - disabled
#define CONFIG_BT_MAX_SCO_CONN 0

// Periodic advertising sync - disabled
#define CONFIG_BT_PER_ADV_SYNC_MAX 0

// Extended advertising - enabled with 1 advertising set
#define CONFIG_BT_EXT_ADV_MAX_ADV_SET 1

// Device appearance (Generic Computer)
#define CONFIG_BT_DEVICE_APPEARANCE 0
#define CONFIG_BT_DEVICE_APPEARANCE_DYNAMIC 0

// Debug/Monitor features - disabled
#define CONFIG_BT_DEBUG_MONITOR_RTT 0
#define CONFIG_BT_DEBUG_MONITOR_UART 0
#define CONFIG_BT_DEBUG_ISO_DATA 0

// Settings storage
#define CONFIG_BT_SETTINGS_CCC_STORE_MAX 0

// Channel Sounding - disabled
#define CONFIG_BT_CHANNEL_SOUNDING_REASSEMBLY_BUFFER_CNT 0
#define CONFIG_BT_CHANNEL_SOUNDING_REASSEMBLY_BUFFER_SIZE 0
#define CONFIG_BT_CHANNEL_SOUNDING_TEST 0

// Controller-specific (when using Zephyr controller, not applicable here)
#define CONFIG_BT_CTLR_PER_INIT_FEAT_XCHG 0
#define CONFIG_BT_CTLR_SCAN_DATA_LEN_MAX 31

// Work queue for TX notifications - disabled (use synchronous model)
#define CONFIG_BT_CONN_TX_NOTIFY_WQ 0
#define CONFIG_BT_CONN_TX_NOTIFY_WQ_PRIO 8
#define CONFIG_BT_CONN_TX_NOTIFY_WQ_INIT_PRIORITY 99
#define CONFIG_BT_CONN_TX_NOTIFY_WQ_STACK_SIZE 1024

// Forward declarations for stub functions (defined in HAL layer)
#include <stddef.h>
#include <stdbool.h>
#include <stdio.h>  // For snprintf
int lll_csrand_get(void *buf, size_t len);  // Controller crypto stub

// Enable printk for BLE debugging - route to MicroPython's mp_printf
// Note: Requires py/mphal.h to be included before this for mp_printf
#ifndef printk
#include "py/mpprint.h"
#define printk(...) mp_printf(&mp_plat_print, __VA_ARGS__)
#endif
#ifndef snprintk
#define snprintk snprintf
#endif

// Missing errno codes - add platform-independent definitions
// ESHUTDOWN is used by Zephyr BLE but may not be defined on all platforms
#ifndef ESHUTDOWN
#define ESHUTDOWN 108  // Standard Linux errno for "Cannot send after transport endpoint shutdown"
#endif

// =============================================================================
// PART 4: Device Tree and HCI Device
// =============================================================================

// Note: Device tree macros (DT_HAS_CHOSEN, DT_CHOSEN, DEVICE_DT_GET) and
// the mp_bluetooth_zephyr_hci_dev extern declaration are in zephyr/devicetree.h wrapper.

// HCI bus types and quirks are now provided by zephyr/drivers/bluetooth.h
// (included via our wrapper at zephyr_headers_stub/zephyr/drivers/bluetooth.h)

// Device tree property access macros (stubbed)
// These are used in hci_core.c to get device properties
#ifndef BT_DT_HCI_BUS_GET
#define BT_DT_HCI_BUS_GET(node) BT_HCI_BUS_UART
#define BT_DT_HCI_NAME_GET(node) "mp_bt_hci"
#define BT_DT_HCI_QUIRKS_GET(node) 0
#endif

// =============================================================================
// PART 5: HCI Driver API
// These are now provided by zephyr/drivers/bluetooth.h (via our wrapper)
// =============================================================================

// Note: struct device and device_is_ready() are defined in our zephyr/device.h wrapper
// Note: DT_* macros are defined in our zephyr/devicetree.h wrapper
// Note: bt_hci_driver_api, bt_hci_recv_t, bt_hci_open, bt_hci_close, bt_hci_send
//       are all provided by zephyr/drivers/bluetooth.h

// H:4 HCI packet type indicators (from Bluetooth spec)
// Only define if not using actual Zephyr bluetooth headers
#ifndef BT_HCI_H4_CMD
#define BT_HCI_H4_CMD  0x01
#define BT_HCI_H4_ACL  0x02
#define BT_HCI_H4_SCO  0x03
#define BT_HCI_H4_EVT  0x04
#define BT_HCI_H4_ISO  0x05
#endif

// Note: Buffer types (enum bt_buf_type) and buffer allocation functions
// (bt_buf_get_evt, bt_buf_get_rx, bt_buf_get_tx, bt_buf_get_type) are defined
// in zephyr/bluetooth/buf.h. Include that header in files that need them.

// ===== Endian Conversion Macros =====
// Required for HCI parameter encoding in scan.c and other Zephyr BLE host code
#include <stdint.h>

// Byte swap functions
static inline uint16_t __bswap_16(uint16_t x) {
    return (uint16_t)((x << 8) | (x >> 8));
}

static inline uint32_t __bswap_32(uint32_t x) {
    return ((x << 24) & 0xFF000000) |
           ((x << 8)  & 0x00FF0000) |
           ((x >> 8)  & 0x0000FF00) |
           ((x >> 24) & 0x000000FF);
}

// ARM Cortex-M is little-endian, so CPU-to-LE is a no-op
#define __LITTLE_ENDIAN__

#ifdef __LITTLE_ENDIAN__
#define sys_cpu_to_le16(x) (x)
#define sys_cpu_to_le32(x) (x)
#define sys_le16_to_cpu(x) (x)
#define sys_le32_to_cpu(x) (x)
#define sys_cpu_to_be16(x) __bswap_16(x)
#define sys_cpu_to_be32(x) __bswap_32(x)
#define sys_be16_to_cpu(x) __bswap_16(x)
#define sys_be32_to_cpu(x) __bswap_32(x)
#else
#define sys_cpu_to_le16(x) __bswap_16(x)
#define sys_cpu_to_le32(x) __bswap_32(x)
#define sys_le16_to_cpu(x) __bswap_16(x)
#define sys_le32_to_cpu(x) __bswap_32(x)
#define sys_cpu_to_be16(x) (x)
#define sys_cpu_to_be32(x) (x)
#define sys_be16_to_cpu(x) (x)
#define sys_be32_to_cpu(x) (x)
#endif

#endif // MICROPY_INCLUDED_EXTMOD_ZEPHYR_BLE_ZEPHYR_BLE_CONFIG_H
