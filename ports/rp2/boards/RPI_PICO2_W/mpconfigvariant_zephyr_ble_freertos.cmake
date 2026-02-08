# Zephyr BLE variant for Pico 2 W (FreeRTOS threading)
# Build with: make BOARD=RPI_PICO2_W BOARD_VARIANT=zephyr_ble_freertos
# Requires extmod/freertos/ (not yet merged)

# Enable Bluetooth with CYW43 hardware
set(MICROPY_PY_BLUETOOTH ON)
set(MICROPY_PY_BLUETOOTH_CYW43 ON)

# Enable threading via FreeRTOS
set(MICROPY_PY_THREAD ON)

# Override bluetooth stack selection (BTstack -> Zephyr)
set(MICROPY_BLUETOOTH_BTSTACK OFF)
set(MICROPY_BLUETOOTH_ZEPHYR ON)
