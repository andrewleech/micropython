set(ZEPHYR_LIB_DIR "${MICROPY_DIR}/lib/zephyr")
set(ZEPHYR_BLE_EXTMOD_DIR "${MICROPY_DIR}/extmod/zephyr_ble")

add_library(micropy_extmod_zephyr_ble INTERFACE)

target_include_directories(micropy_extmod_zephyr_ble INTERFACE
    ${MICROPY_DIR}/
    ${MICROPY_PORT_DIR}/
    ${ZEPHYR_BLE_EXTMOD_DIR}/
    ${ZEPHYR_BLE_EXTMOD_DIR}/hal/
    # Stub headers must come BEFORE real Zephyr headers to shadow them
    ${ZEPHYR_BLE_EXTMOD_DIR}/zephyr_headers_stub/
    ${ZEPHYR_LIB_DIR}/include
    ${ZEPHYR_LIB_DIR}/subsys/bluetooth
    ${ZEPHYR_LIB_DIR}/subsys/bluetooth/host
    # TinyCrypt for AES-128 encryption
    ${MICROPY_DIR}/lib/mynewt-nimble/ext/tinycrypt/include
)

target_sources(micropy_extmod_zephyr_ble INTERFACE
    # MicroPython bindings
    ${ZEPHYR_BLE_EXTMOD_DIR}/modbluetooth_zephyr.c
    ${ZEPHYR_BLE_EXTMOD_DIR}/hci_driver_stub.c
    ${ZEPHYR_BLE_EXTMOD_DIR}/net_buf_pool_registry.c

    # HAL abstraction layer
    ${ZEPHYR_BLE_EXTMOD_DIR}/hal/zephyr_ble_timer.c
    ${ZEPHYR_BLE_EXTMOD_DIR}/hal/zephyr_ble_work.c
    ${ZEPHYR_BLE_EXTMOD_DIR}/hal/zephyr_ble_sem.c
    ${ZEPHYR_BLE_EXTMOD_DIR}/hal/zephyr_ble_mutex.c
    ${ZEPHYR_BLE_EXTMOD_DIR}/hal/zephyr_ble_fifo.c
    ${ZEPHYR_BLE_EXTMOD_DIR}/hal/zephyr_ble_mem_slab.c
    ${ZEPHYR_BLE_EXTMOD_DIR}/hal/zephyr_ble_kernel.c
    ${ZEPHYR_BLE_EXTMOD_DIR}/hal/zephyr_ble_poll.c
    ${ZEPHYR_BLE_EXTMOD_DIR}/hal/zephyr_ble_settings.c
    ${ZEPHYR_BLE_EXTMOD_DIR}/hal/zephyr_ble_psa_crypto.c
    ${ZEPHYR_BLE_EXTMOD_DIR}/hal/zephyr_ble_util.c
    ${ZEPHYR_BLE_EXTMOD_DIR}/hal/zephyr_ble_arch.c
    ${ZEPHYR_BLE_EXTMOD_DIR}/hal/zephyr_ble_crypto.c
    ${ZEPHYR_BLE_EXTMOD_DIR}/hal/zephyr_ble_crypto_stubs.c
    ${ZEPHYR_BLE_EXTMOD_DIR}/hal/zephyr_ble_monitor_stubs.c
    ${ZEPHYR_BLE_EXTMOD_DIR}/hal/zephyr_ble_feature_stubs.c
    ${ZEPHYR_BLE_EXTMOD_DIR}/hal/zephyr_ble_array_size_stub.c
    ${ZEPHYR_BLE_EXTMOD_DIR}/hal/zephyr_ble_port_stubs.c
    ${ZEPHYR_BLE_EXTMOD_DIR}/hal/zephyr_ble_gatt_alloc.c
    ${ZEPHYR_BLE_EXTMOD_DIR}/hal/zephyr_ble_h4.c

    # Zephyr net_buf library
    ${ZEPHYR_LIB_DIR}/lib/net_buf/buf.c
    ${ZEPHYR_LIB_DIR}/lib/net_buf/buf_simple.c

    # Zephyr BLE common sources
    ${ZEPHYR_LIB_DIR}/subsys/bluetooth/common/addr.c
    ${ZEPHYR_LIB_DIR}/subsys/bluetooth/common/rpa.c

    # Zephyr BLE host core sources (Phase 1: Core + SMP/Keys/ECC/Crypto)
    ${ZEPHYR_LIB_DIR}/subsys/bluetooth/host/hci_core.c
    ${ZEPHYR_LIB_DIR}/subsys/bluetooth/host/hci_common.c
    ${ZEPHYR_LIB_DIR}/subsys/bluetooth/host/id.c
    ${ZEPHYR_LIB_DIR}/subsys/bluetooth/host/addr.c
    ${ZEPHYR_LIB_DIR}/subsys/bluetooth/host/buf.c
    ${ZEPHYR_LIB_DIR}/subsys/bluetooth/host/uuid.c
    ${ZEPHYR_LIB_DIR}/subsys/bluetooth/host/conn.c
    ${ZEPHYR_LIB_DIR}/subsys/bluetooth/host/l2cap.c
    ${ZEPHYR_LIB_DIR}/subsys/bluetooth/host/att.c
    ${ZEPHYR_LIB_DIR}/subsys/bluetooth/host/gatt.c
    ${ZEPHYR_LIB_DIR}/subsys/bluetooth/host/adv.c
    ${ZEPHYR_LIB_DIR}/subsys/bluetooth/host/scan.c
    ${ZEPHYR_LIB_DIR}/subsys/bluetooth/host/data.c
    ${ZEPHYR_LIB_DIR}/subsys/bluetooth/host/keys.c
    ${ZEPHYR_LIB_DIR}/subsys/bluetooth/host/smp.c
    # ecc.c and crypto_psa.c are replaced by zephyr_ble_crypto.c (TinyCrypt implementation)
    # zephyr_ble_crypto_stubs.c provides stubs for unimplemented crypto (e.g., controller crypto)

    # TinyCrypt crypto library for BLE pairing (Legacy + SC)
    ${MICROPY_DIR}/lib/mynewt-nimble/ext/tinycrypt/src/aes_encrypt.c   # AES-128-ECB (Legacy pairing)
    ${MICROPY_DIR}/lib/mynewt-nimble/ext/tinycrypt/src/cmac_mode.c     # AES-CMAC (SC crypto functions)
    ${MICROPY_DIR}/lib/mynewt-nimble/ext/tinycrypt/src/ecc.c           # ECC P-256 (SC public key)
    ${MICROPY_DIR}/lib/mynewt-nimble/ext/tinycrypt/src/ecc_dh.c        # ECDH (SC shared secret)
    ${MICROPY_DIR}/lib/mynewt-nimble/ext/tinycrypt/src/utils.c         # Utility functions
)

# CRITICAL FIX: Disable -fdata-sections for Zephyr sources using STRUCT_SECTION_ITERABLE
#
# Zephyr's STRUCT_SECTION_ITERABLE macro requires structures to be placed in named linker
# sections (e.g., ._net_buf_pool.static.*) so they can be iterated at runtime via
# TYPE_SECTION_START/TYPE_SECTION_END symbols. When -fdata-sections is enabled, GCC splits
# data objects into individual sections (e.g., .data.hci_cmd_pool), preventing the linker
# from collecting them into the expected section array.
#
# Without this fix, net_buf pools end up scattered across memory, causing TYPE_SECTION_START
# to point to the wrong location. When code tries to look up a pool by ID using
# net_buf_pool_get(id), it reads garbage memory, resulting in invalid function pointers
# (observed as r3=0x10 when calling pool->alloc->cb->alloc, causing HardFault).
#
# Files affected:
# - buf.c: Defines net_buf_pool_get() which uses TYPE_SECTION_START(net_buf_pool)
# - hci_core.c: Defines hci_cmd_pool using NET_BUF_POOL_FIXED_DEFINE
# - buf.c (host): Defines additional BLE buffer pools
#
set_source_files_properties(
    ${ZEPHYR_LIB_DIR}/lib/net_buf/buf.c
    ${ZEPHYR_LIB_DIR}/subsys/bluetooth/host/hci_core.c
    ${ZEPHYR_LIB_DIR}/subsys/bluetooth/host/buf.c
    ${ZEPHYR_LIB_DIR}/subsys/bluetooth/host/smp.c    # BT_L2CAP_FIXED_CHANNEL_DEFINE uses STRUCT_SECTION_ITERABLE
    PROPERTIES COMPILE_FLAGS "-fno-data-sections"
)

# Debug output for Zephyr BLE (disabled by default)
# Set ZEPHYR_BLE_DEBUG=1 in CMake command to enable debug output
if(NOT DEFINED ZEPHYR_BLE_DEBUG)
    set(ZEPHYR_BLE_DEBUG 0)
endif()

# When debug is enabled, also route Zephyr printk to console
if(ZEPHYR_BLE_DEBUG EQUAL 1)
    set(ZEPHYR_BLE_PRINTK_DEBUG 1)
else()
    set(ZEPHYR_BLE_PRINTK_DEBUG 0)
endif()
if(NOT DEFINED ZEPHYR_BLE_SETTINGS_NOOP)
    set(ZEPHYR_BLE_SETTINGS_NOOP 0)
endif()

target_compile_definitions(micropy_extmod_zephyr_ble INTERFACE
    MICROPY_BLUETOOTH_ZEPHYR=1
    MICROPY_PY_BLUETOOTH_USE_SYNC_EVENTS=1
    MICROPY_PY_BLUETOOTH_ENABLE_PAIRING_BONDING=1
    ZEPHYR_BLE_DEBUG=${ZEPHYR_BLE_DEBUG}
    ZEPHYR_BLE_PRINTK_DEBUG=${ZEPHYR_BLE_PRINTK_DEBUG}
    ZEPHYR_BLE_SETTINGS_NOOP=${ZEPHYR_BLE_SETTINGS_NOOP}
)

# Note: No force-include needed - the stub toolchain/gcc.h includes autoconf.h
# before forwarding to the real gcc.h, ensuring CONFIG_ARM is defined in time.

# Warning suppressions for Zephyr BLE integration (C files only)
# These are unavoidable due to integrating Zephyr (designed for its own build system) into MicroPython:
# - Wno-error: Required because Zephyr headers and our stubs have unavoidable macro redefinitions
#   (GCC's macro redefinition warnings can't be individually disabled like Clang's -Wno-macro-redefined)
# - implicit-function-declaration: ARRAY_SIZE stub resolved at link time for zero-sized arrays
# - unused-value: LOG_DBG macro uses comma operator (no-op when logging disabled)
# - array-bounds: Zero-sized arrays when CONFIG options disable features
# - attributes: UNALIGNED_GET macro applies __packed__ to pointer types
# - unused-function/variable/but-set-variable: Debug/logging code compiled out by CONFIG
# - format: Printf format strings use %x for uint32_t (platform-dependent type)
target_compile_options(micropy_extmod_zephyr_ble INTERFACE
    $<$<COMPILE_LANGUAGE:C>:-Wno-error>
    $<$<COMPILE_LANGUAGE:C>:-Wno-implicit-function-declaration>
    $<$<COMPILE_LANGUAGE:C>:-Wno-unused-value>
    $<$<COMPILE_LANGUAGE:C>:-Wno-array-bounds>
    $<$<COMPILE_LANGUAGE:C>:-Wno-attributes>
    $<$<COMPILE_LANGUAGE:C>:-Wno-unused-function>
    $<$<COMPILE_LANGUAGE:C>:-Wno-unused-variable>
    $<$<COMPILE_LANGUAGE:C>:-Wno-unused-but-set-variable>
    $<$<COMPILE_LANGUAGE:C>:-Wno-format>
)
