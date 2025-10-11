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
    ${ZEPHYR_BLE_EXTMOD_DIR}/modbluetooth_zephyr.c
    ${ZEPHYR_BLE_EXTMOD_DIR}/hci_driver_stub.c
    ${ZEPHYR_BLE_EXTMOD_DIR}/hal/zephyr_ble_timer.c
    ${ZEPHYR_BLE_EXTMOD_DIR}/hal/zephyr_ble_work.c
    ${ZEPHYR_BLE_EXTMOD_DIR}/hal/zephyr_ble_sem.c
    ${ZEPHYR_BLE_EXTMOD_DIR}/hal/zephyr_ble_mutex.c
    ${ZEPHYR_BLE_EXTMOD_DIR}/hal/zephyr_ble_kernel.c
    ${ZEPHYR_BLE_EXTMOD_DIR}/hal/zephyr_ble_poll.c
    ${ZEPHYR_LIB_DIR}/lib/net_buf/buf.c
    ${ZEPHYR_LIB_DIR}/lib/net_buf/buf_simple.c
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
    ${ZEPHYR_LIB_DIR}/subsys/bluetooth/host/smp_null.c
    ${ZEPHYR_LIB_DIR}/subsys/bluetooth/host/data.c
)

target_compile_definitions(micropy_extmod_zephyr_ble INTERFACE
    MICROPY_BLUETOOTH_ZEPHYR=1
    MICROPY_PY_BLUETOOTH_USE_SYNC_EVENTS=1
    MICROPY_PY_BLUETOOTH_ENABLE_PAIRING_BONDING=1
)

# Force-include config header before any other includes to:
# - Pre-define header guards for unwanted headers
# - Prevent macro conflicts with platform SDKs
target_compile_options(micropy_extmod_zephyr_ble INTERFACE
    -include ${ZEPHYR_BLE_EXTMOD_DIR}/zephyr_ble_config.h
)

# TODO: Technical Debt - Warnings disabled due to macro conflicts with platform SDKs
# Future work should resolve:
# - __CONCAT redefinition (Pico SDK vs Zephyr)
# - __weak redefinition (Pico SDK vs Zephyr toolchain.h)
# - MICROPY_PY_BLUETOOTH_ENTER redefinition (mpconfigport.h vs zephyr_ble_atomic.h)
# These conflicts arise from incompatibilities between platform SDK macros and Zephyr's
# expectations. Proper resolution requires either:
# 1. Namespace isolation for Zephyr code
# 2. Coordination with platform SDK maintainers
# 3. More selective header inclusion strategy
target_compile_options(micropy_extmod_zephyr_ble INTERFACE
    -Wno-error
    -w  # Suppress all warnings for Zephyr BLE (gcc-compatible)
)
