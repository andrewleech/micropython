set(ZEPHYR_LIB_DIR "${MICROPY_DIR}/lib/zephyr")
set(ZEPHYR_BLE_EXTMOD_DIR "${MICROPY_DIR}/extmod/zephyr_ble")

add_library(micropy_extmod_zephyr_ble INTERFACE)

target_include_directories(micropy_extmod_zephyr_ble INTERFACE
    ${MICROPY_DIR}/
    ${MICROPY_PORT_DIR}/
    ${ZEPHYR_BLE_EXTMOD_DIR}/
    ${ZEPHYR_BLE_EXTMOD_DIR}/hal/
    ${ZEPHYR_LIB_DIR}/include
    ${ZEPHYR_LIB_DIR}/subsys/bluetooth
    ${ZEPHYR_LIB_DIR}/subsys/bluetooth/host
)

target_sources(micropy_extmod_zephyr_ble INTERFACE
    # MicroPython bindings
    ${ZEPHYR_BLE_EXTMOD_DIR}/modbluetooth_zephyr.c
    ${ZEPHYR_BLE_EXTMOD_DIR}/hci_driver_stub.c

    # HAL abstraction layer
    ${ZEPHYR_BLE_EXTMOD_DIR}/hal/zephyr_ble_timer.c
    ${ZEPHYR_BLE_EXTMOD_DIR}/hal/zephyr_ble_work.c
    ${ZEPHYR_BLE_EXTMOD_DIR}/hal/zephyr_ble_sem.c
    ${ZEPHYR_BLE_EXTMOD_DIR}/hal/zephyr_ble_mutex.c
    ${ZEPHYR_BLE_EXTMOD_DIR}/hal/zephyr_ble_kernel.c
    ${ZEPHYR_BLE_EXTMOD_DIR}/hal/zephyr_ble_poll.c
    ${ZEPHYR_BLE_EXTMOD_DIR}/hal/zephyr_ble_settings.c
    ${ZEPHYR_BLE_EXTMOD_DIR}/hal/zephyr_ble_psa_crypto.c
    ${ZEPHYR_BLE_EXTMOD_DIR}/hal/zephyr_ble_util.c
    ${ZEPHYR_BLE_EXTMOD_DIR}/hal/zephyr_ble_arch.c
    ${ZEPHYR_BLE_EXTMOD_DIR}/hal/zephyr_ble_crypto_stubs.c
    ${ZEPHYR_BLE_EXTMOD_DIR}/hal/zephyr_ble_monitor_stubs.c
    ${ZEPHYR_BLE_EXTMOD_DIR}/hal/zephyr_ble_feature_stubs.c
    ${ZEPHYR_BLE_EXTMOD_DIR}/hal/zephyr_ble_array_size_stub.c

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
    ${ZEPHYR_LIB_DIR}/subsys/bluetooth/host/ecc.c
    ${ZEPHYR_LIB_DIR}/subsys/bluetooth/host/crypto_psa.c
)

target_compile_definitions(micropy_extmod_zephyr_ble INTERFACE
    MICROPY_BLUETOOTH_ZEPHYR=1
    MICROPY_PY_BLUETOOTH_USE_SYNC_EVENTS=1
    MICROPY_PY_BLUETOOTH_ENABLE_PAIRING_BONDING=1
)

# Force-include config header before any other includes (C files only, not ASM) to:
# - Pre-define header guards for unwanted headers
# - Prevent macro conflicts with platform SDKs
target_compile_options(micropy_extmod_zephyr_ble INTERFACE
    $<$<COMPILE_LANGUAGE:C>:-include ${ZEPHYR_BLE_EXTMOD_DIR}/zephyr_ble_config.h>
)

# Selective warning suppressions for Zephyr BLE integration (C files only)
# Only suppress specific warnings that are unavoidable due to integration challenges:
# - implicit-function-declaration: ARRAY_SIZE stub resolved at link time for zero-sized arrays
# - unused-value: LOG_DBG macro uses comma operator (no-op when logging disabled)
# - array-bounds: Zero-sized arrays when CONFIG options disable features
# - attributes: UNALIGNED_GET macro applies __packed__ to pointer types
# - unused-function/variable/but-set-variable: Debug/logging code compiled out by CONFIG
# - format: Printf format strings use %x for uint32_t (platform-dependent type)
target_compile_options(micropy_extmod_zephyr_ble INTERFACE
    $<$<COMPILE_LANGUAGE:C>:-Wno-implicit-function-declaration>
    $<$<COMPILE_LANGUAGE:C>:-Wno-unused-value>
    $<$<COMPILE_LANGUAGE:C>:-Wno-array-bounds>
    $<$<COMPILE_LANGUAGE:C>:-Wno-attributes>
    $<$<COMPILE_LANGUAGE:C>:-Wno-unused-function>
    $<$<COMPILE_LANGUAGE:C>:-Wno-unused-variable>
    $<$<COMPILE_LANGUAGE:C>:-Wno-unused-but-set-variable>
    $<$<COMPILE_LANGUAGE:C>:-Wno-format>
)
