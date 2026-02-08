# Zephyr BLE variant for Pico 2 W (cooperative polling, no FreeRTOS)
# Build with: make BOARD=RPI_PICO2_W BOARD_VARIANT=zephyr_ble

# Enable Bluetooth with CYW43 hardware
set(MICROPY_PY_BLUETOOTH ON)
set(MICROPY_PY_BLUETOOTH_CYW43 ON)

# Disable threading - use cooperative polling instead of FreeRTOS tasks
set(MICROPY_PY_THREAD OFF)

# Override bluetooth stack selection (BTstack -> Zephyr)
set(MICROPY_BLUETOOTH_BTSTACK OFF)
set(MICROPY_BLUETOOTH_ZEPHYR ON)
