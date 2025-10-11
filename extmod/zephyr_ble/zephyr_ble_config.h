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

#include <stddef.h>
#include <stdint.h>

// Zephyr BLE Configuration
// Maps Zephyr Kconfig options to static defines for MicroPython

// Forward declarations for Zephyr types referenced in headers
// arch_esf is the architecture exception stack frame (used in hci_vs.h)
struct arch_esf;

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

// Don't block the drivers/bluetooth.h header - we provide a wrapper for it
// The wrapper includes HCI driver API without device tree dependencies

// Problematic Zephyr system headers that conflict with our HAL wrappers
#define INCLUDE_ZEPHYR_SYS_ITERABLE_SECTIONS_H_
#define ZEPHYR_INCLUDE_TOOLCHAIN_H_
#define ZEPHYR_INCLUDE_KERNEL_THREAD_STACK_H

// Stub macros for iterable sections (feature disabled in MicroPython)
// These macros are normally defined in sys/iterable_sections.h
#define STRUCT_SECTION_ITERABLE(struct_type, varname) struct struct_type varname

// STRUCT_SECTION_FOREACH iterates over linker sections (disabled in MicroPython)
// This is used for callback registration - stub to empty loop that never executes
#define STRUCT_SECTION_FOREACH(struct_type, varname) \
    for (struct struct_type *varname = NULL; varname != NULL; varname = NULL)

// System initialization macro (no-op in MicroPython)
// SYS_INIT(func, level, priority) registers an init function
// In MicroPython we call init functions explicitly, so stub this out
#define SYS_INIT(func, level, priority) \
    static inline int __sys_init_##func(void) { return 0; }

// Init levels (not used, but referenced in SYS_INIT calls)
#define POST_KERNEL 0

// Additional iterable section macros used by net_buf
// Note: In MicroPython we don't use linker sections, so these return NULL/0
// This disables net_buf pool iteration which is only used for debug/stats
#define STRUCT_SECTION_START_EXTERN(struct_type) /* nothing */
#define STRUCT_SECTION_START(struct_type) ((struct_type *)NULL)
#define STRUCT_SECTION_END(struct_type) ((struct_type *)NULL)
#define STRUCT_SECTION_COUNT(struct_type) 0
#define STRUCT_SECTION_GET(struct_type, i, dst) \
    do { (void)(i); *(dst) = NULL; } while (0)
// TYPE_SECTION_* macros don't get the struct prefix
// These are used for pointer arithmetic in pool_id(), so cast to struct pointer
#define TYPE_SECTION_START(secname) ((struct secname *)NULL)
#define TYPE_SECTION_END(secname) ((struct secname *)NULL)
#define TYPE_SECTION_COUNT(secname) 0

// Atomic bitmap operations (simplified stubs)
// Normally defined in sys/atomic.h
#define ATOMIC_DEFINE(name, num_bits) unsigned long name[((num_bits) + 31) / 32]
#define ATOMIC_INIT(value) (value)

// Conditional compilation helpers
// __COND_CODE(flag, if_1_code, if_0_code) - expands to if_1_code if flag=1, if_0_code if flag=0
#define __COND_CODE(flag, if_1_code, if_0_code) \
    __COND_CODE_IMPL(flag, if_1_code, if_0_code)

// Helper for __COND_CODE
#define __COND_CODE_IMPL(flag, if_1_code, if_0_code) \
    __COND_CODE_##flag(if_1_code, if_0_code)

#define __COND_CODE_0(if_1_code, if_0_code) if_0_code
#define __COND_CODE_1(if_1_code, if_0_code) if_1_code

// IF_ENABLED(config, (code)) expands to code if config=1, empty otherwise
// When config=0 or undefined, expands to nothing
// Need indirection to force config macro expansion before concatenation
#define IF_ENABLED(config, code) \
    __IF_ENABLED_IMPL(config, code)

#define __IF_ENABLED_IMPL(config, code) \
    __IF_ENABLED_##config(code)

#define __IF_ENABLED_0(code)  /* empty - expands to nothing */
#define __IF_ENABLED_1(code) code
// Handle undefined config values (expands to literal token, which we ignore)
#define __IF_ENABLED_CONFIG_BT_SETTINGS_DELAYED_STORE(code) /* empty */

// Note: IS_ENABLED is defined in sys/util_macro.h

// COND_CODE_1 is like COND_CODE but checks if value is 1 specifically
// When flag=1, expands to if_1_code; when flag=0, expands to if_0_code
#define COND_CODE_1(flag, if_1_code, if_0_code) \
    __COND_CODE_1_IMPL(flag, if_1_code, if_0_code)

#define __COND_CODE_1_IMPL(flag, if_1_code, if_0_code) \
    __COND_CODE_1_##flag(if_1_code, if_0_code)

// Helper macros: parameter names match the code path they select
// __COND_CODE_1_0 is invoked when flag=0, so it returns the "false" branch (if_0_code)
// __COND_CODE_1_1 is invoked when flag=1, so it returns the "true" branch (if_1_code)
#define __COND_CODE_1_0(true_branch, false_branch) false_branch
#define __COND_CODE_1_1(true_branch, false_branch) true_branch

// Net buf configuration (must be before net_buf.h is included)
#define CONFIG_NET_BUF_ALIGNMENT 0
#define CONFIG_NET_BUF_WARN_ALLOC_INTERVAL 0
#define CONFIG_NET_BUF_LOG_LEVEL 0
#define CONFIG_NET_BUF_POOL_USAGE 0

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

#ifndef BITS_PER_BYTE
#define BITS_PER_BYTE 8
#endif

#ifndef IN_RANGE
#define IN_RANGE(val, min, max) ((val) >= (min) && (val) <= (max))
#endif

#ifndef BUILD_ASSERT
// Simplified build assertion that handles both 1 and 2 argument versions
// Uses variadic macro to accept optional message parameter
// MicroPython always uses C11 or later, so we can rely on _Static_assert
#define BUILD_ASSERT(cond, ...) _Static_assert(cond, #cond)
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
#define __noinit
#endif

#ifndef FLEXIBLE_ARRAY_DECLARE
// Flexible array member declaration (C99 feature)
// Add dummy member if needed to avoid "struct with no named members" error
#define FLEXIBLE_ARRAY_DECLARE(type, name) \
    uint8_t __flex_dummy; \
    type name[]
#endif

// =============================================================================
// PART 3: Zephyr BLE Stack Configuration
// =============================================================================

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
#define CONFIG_BT_MAX_PAIRED 4

// --- GATT Support ---
#define CONFIG_BT_GATT_CLIENT 1
#define CONFIG_BT_GATT_DYNAMIC_DB 1
#define CONFIG_BT_GATT_SERVICE_CHANGED 1
#define CONFIG_BT_GATT_CACHING 0
#define CONFIG_BT_ATT_PREPARE_COUNT 0

// --- Security (SMP) ---
#define CONFIG_BT_SMP 1
#define CONFIG_BT_SIGNING 0
#define CONFIG_BT_SMP_SC_PAIR_ONLY 0
#define CONFIG_BT_SMP_SC_ONLY 0
#define CONFIG_BT_SMP_OOB_LEGACY_PAIR_ONLY 0
#define CONFIG_BT_SMP_ENFORCE_MITM 0
#define CONFIG_BT_SMP_USB_HCI_CTLR_WORKAROUND 0
#define CONFIG_BT_SMP_ALLOW_UNAUTH_OVERWRITE 1
#define CONFIG_BT_FIXED_PASSKEY 0
#define CONFIG_BT_USE_DEBUG_KEYS 0
#define CONFIG_BT_PASSKEY_MAX 999999
#define CONFIG_BT_SMP_MIN_ENC_KEY_SIZE 7  // Minimum encryption key size (7-16 bytes)
#define BT_SMP_MIN_ENC_KEY_SIZE CONFIG_BT_SMP_MIN_ENC_KEY_SIZE
#define CONFIG_BT_PRIVACY 1
#define CONFIG_BT_RPA 1
#define CONFIG_BT_CTLR_PRIVACY 0  // No controller privacy (host-only)

// --- L2CAP ---
#define CONFIG_BT_L2CAP_TX_BUF_COUNT 4
#define CONFIG_BT_L2CAP_TX_MTU 65  // Default L2CAP MTU size

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
#define CONFIG_BT_BUF_ACL_TX_SIZE 27
#define CONFIG_BT_BUF_ACL_RX_COUNT 8
#define CONFIG_BT_BUF_ACL_RX_SIZE 27
#define CONFIG_BT_BUF_ACL_RX_COUNT_EXTRA CONFIG_BT_MAX_CONN

// Event buffers
#define CONFIG_BT_BUF_EVT_RX_COUNT 16
#define CONFIG_BT_BUF_EVT_RX_SIZE 68
#define CONFIG_BT_BUF_EVT_DISCARDABLE_COUNT 3
#define CONFIG_BT_BUF_EVT_DISCARDABLE_SIZE 43

// Command buffers
#define CONFIG_BT_BUF_CMD_TX_COUNT 4
#define CONFIG_BT_BUF_CMD_TX_SIZE 68

// Flow control (disabled for simplicity in MicroPython)
#define CONFIG_BT_HCI_ACL_FLOW_CONTROL 0

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
#define CONFIG_BT_EXT_ADV 0

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

// --- Shell/Testing - Disabled ---
#define CONFIG_BT_SHELL 0
#define CONFIG_BT_TESTING 0

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
#define CONFIG_BT_RECV_WORKQ_SYS 1
#define CONFIG_BT_RECV_WORKQ_BT 0

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
#define CONFIG_SYS_CLOCK_TICKS_PER_SEC 1000
#define MSEC_PER_SEC 1000

// RPA (Resolvable Private Address) timeout in seconds
#define CONFIG_BT_RPA_TIMEOUT 900  // 15 minutes

// --- Assert Configuration ---
#ifdef NDEBUG
#define CONFIG_ASSERT 0
#else
#define CONFIG_ASSERT 1
#endif

// Bluetooth-specific assert macros (from subsys/bluetooth/common/assert.h)
// These are defined here to ensure they're always available when BLE code is compiled
#define CONFIG_BT_ASSERT 0  // Use simple __ASSERT fallback, not verbose BT_ASSERT
#define CONFIG_BT_ASSERT_VERBOSE 0
#define CONFIG_BT_ASSERT_PANIC 0

// When CONFIG_BT_ASSERT=0, BT_ASSERT falls back to __ASSERT macros (defined in kernel.h)
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
#define CONFIG_BT_SMP_LOG_LEVEL 0
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
#define CONFIG_BT_BONDABLE 0
#define CONFIG_BT_BONDING_REQUIRED 0
#define CONFIG_BT_BONDABLE_PER_CONNECTION 0
#define CONFIG_BT_AUTO_PHY_UPDATE 0
#define CONFIG_BT_AUTO_DATA_LEN_UPDATE 0
#define CONFIG_BT_CONN_DISABLE_SECURITY 0
#define CONFIG_BT_CONN_CHECK_NULL_BEFORE_CREATE 0
#define CONFIG_BT_CONN_PARAM_ANY 0
#define CONFIG_BT_CONN_TX 0
#define CONFIG_BT_CONN_DYNAMIC_CALLBACKS 0
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

// Extended advertising - disabled
#define CONFIG_BT_EXT_ADV_MAX_ADV_SET 0

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
int lll_csrand_get(void *buf, size_t len);  // Controller crypto stub

// =============================================================================
// PART 4: Device Tree and HCI Device
// =============================================================================

// Note: Device tree macros (DT_HAS_CHOSEN, DT_CHOSEN, DEVICE_DT_GET) and
// the mp_bluetooth_zephyr_hci_dev extern declaration are in zephyr/devicetree.h wrapper.

// HCI bus types (from zephyr/drivers/bluetooth.h)
#ifndef BT_HCI_BUS_UART
#define BT_HCI_BUS_UART 0
#define BT_HCI_BUS_USB 1
#define BT_HCI_BUS_SDIO 2
#define BT_HCI_BUS_SPI 3
#define BT_HCI_BUS_IPC 4
#define BT_HCI_BUS_VIRTUAL 5
#endif

// HCI quirks (from zephyr/drivers/bluetooth.h)
#ifndef BT_HCI_QUIRK_NO_RESET
#define BT_HCI_QUIRK_NO_RESET BIT(0)
#define BT_HCI_QUIRK_NO_AUTO_DLE BIT(1)
#endif

// Device tree property access macros (stubbed)
// These are used in hci_core.c to get device properties
#ifndef BT_DT_HCI_BUS_GET
#define BT_DT_HCI_BUS_GET(node) BT_HCI_BUS_UART
#define BT_DT_HCI_NAME_GET(node) "mp_bt_hci"
#define BT_DT_HCI_QUIRKS_GET(node) 0
#endif

// =============================================================================
// PART 5: HCI Driver API Structures
// Replaces zephyr/drivers/bluetooth.h which we block
// =============================================================================

// Note: struct device and device_is_ready() are defined in our zephyr/device.h wrapper
// Note: DT_* macros are defined in our zephyr/devicetree.h wrapper

struct net_buf;
struct device;  // Forward declaration (defined in zephyr/device.h wrapper)

// HCI receive callback type
typedef int (*bt_hci_recv_t)(const struct device *dev, struct net_buf *buf);

// HCI driver API structure
struct bt_hci_driver_api {
    int (*open)(const struct device *dev, bt_hci_recv_t recv);
    int (*close)(const struct device *dev);
    int (*send)(const struct device *dev, struct net_buf *buf);
};

// Note: HCI driver API wrappers (bt_hci_open, bt_hci_close, bt_hci_send)
// are defined in zephyr/drivers/bluetooth.h wrapper

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

#endif // MICROPY_INCLUDED_EXTMOD_ZEPHYR_BLE_ZEPHYR_BLE_CONFIG_H
