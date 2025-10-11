# Board variant for testing Zephyr BLE stack on Pico 2 W
# Build with: make BOARD=RPI_PICO2_W BOARD_VARIANT=zephyr

# Enable Bluetooth with CYW43 hardware
set(MICROPY_PY_BLUETOOTH ON)
set(MICROPY_PY_BLUETOOTH_CYW43 ON)

# Override bluetooth stack selection (BTstack -> Zephyr)
set(MICROPY_BLUETOOTH_BTSTACK OFF)
set(MICROPY_BLUETOOTH_ZEPHYR ON)
