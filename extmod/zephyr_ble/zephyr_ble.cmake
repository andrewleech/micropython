set(ZEPHYR_LIB_DIR "${MICROPY_DIR}/lib/zephyr")
set(ZEPHYR_BLE_EXTMOD_DIR "${MICROPY_DIR}/extmod/zephyr_ble")

add_library(micropy_extmod_zephyr_ble INTERFACE)

target_include_directories(micropy_extmod_zephyr_ble INTERFACE
    ${MICROPY_DIR}/
    ${MICROPY_PORT_DIR}/
    ${ZEPHYR_BLE_EXTMOD_DIR}/
    ${ZEPHYR_BLE_EXTMOD_DIR}/hal/
    ${ZEPHYR_LIB_DIR}/include
)

target_sources(micropy_extmod_zephyr_ble INTERFACE
    ${ZEPHYR_BLE_EXTMOD_DIR}/modbluetooth_zephyr.c
    ${ZEPHYR_BLE_EXTMOD_DIR}/hal/zephyr_ble_timer.c
    ${ZEPHYR_BLE_EXTMOD_DIR}/hal/zephyr_ble_work.c
    ${ZEPHYR_BLE_EXTMOD_DIR}/hal/zephyr_ble_sem.c
    ${ZEPHYR_BLE_EXTMOD_DIR}/hal/zephyr_ble_mutex.c
    ${ZEPHYR_BLE_EXTMOD_DIR}/hal/zephyr_ble_kernel.c
    ${ZEPHYR_BLE_EXTMOD_DIR}/hal/zephyr_ble_poll.c
)

# TODO: Add Zephyr BLE host sources after dependency analysis

target_compile_definitions(micropy_extmod_zephyr_ble INTERFACE
    MICROPY_BLUETOOTH_ZEPHYR=1
    MICROPY_PY_BLUETOOTH_USE_SYNC_EVENTS=1
    MICROPY_PY_BLUETOOTH_ENABLE_PAIRING_BONDING=1
)
