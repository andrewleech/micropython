# Board variant for testing Zephyr BLE stack on Pico W
# Build with: make BOARD=RPI_PICO_W BOARD_VARIANT=zephyr
message(STATUS "Loading Zephyr BLE variant for Pico W")

# Allocate 16KB C heap for GATT service/UUID allocations
# (default is 0, which uses tiny 8KB FreeRTOS heap)
set(MICROPY_C_HEAP_SIZE 16384)

# Override bluetooth stack selection
set(MICROPY_BLUETOOTH_BTSTACK OFF)
set(MICROPY_BLUETOOTH_ZEPHYR ON)

# Debug output disabled - causes NLR crashes in work thread
# Pairing-specific debug added directly to modbluetooth_zephyr.c
# set(ZEPHYR_BLE_DEBUG 1)

# Force fetch picotool from git (version mismatch workaround)
set(PICOTOOL_FORCE_FETCH_FROM_GIT ON)

# Note: MICROPY_PY_BLUETOOTH and MICROPY_PY_BLUETOOTH_CYW43 are already
# set in the base mpconfigboard.cmake and remain enabled
