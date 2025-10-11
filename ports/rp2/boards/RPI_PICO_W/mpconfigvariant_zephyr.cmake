# Board variant for testing Zephyr BLE stack on Pico W
# Build with: make BOARD=RPI_PICO_W BOARD_VARIANT=zephyr

# Override bluetooth stack selection
set(MICROPY_BLUETOOTH_BTSTACK OFF)
set(MICROPY_BLUETOOTH_ZEPHYR ON)

# Note: MICROPY_PY_BLUETOOTH and MICROPY_PY_BLUETOOTH_CYW43 are already
# set in the base mpconfigboard.cmake and remain enabled
