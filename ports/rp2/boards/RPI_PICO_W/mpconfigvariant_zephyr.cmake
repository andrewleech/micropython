# Board variant for testing Zephyr BLE stack on Pico W
# Build with: make BOARD=RPI_PICO_W BOARD_VARIANT=zephyr
message(STATUS "Loading Zephyr BLE variant for Pico W")

# Override bluetooth stack selection
set(MICROPY_BLUETOOTH_BTSTACK OFF)
set(MICROPY_BLUETOOTH_ZEPHYR ON)

# Debug output disabled - causes NLR crashes in work thread
# set(ZEPHYR_BLE_DEBUG 1)

# Force fetch picotool from git (version mismatch workaround)
set(PICOTOOL_FORCE_FETCH_FROM_GIT ON)

# Note: MICROPY_PY_BLUETOOTH and MICROPY_PY_BLUETOOTH_CYW43 are already
# set in the base mpconfigboard.cmake and remain enabled
