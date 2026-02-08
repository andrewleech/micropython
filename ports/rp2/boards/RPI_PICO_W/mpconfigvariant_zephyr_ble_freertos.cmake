# Zephyr BLE variant for Pico W (FreeRTOS threading)
# Build with: make BOARD=RPI_PICO_W BOARD_VARIANT=zephyr_ble_freertos
# Requires extmod/freertos/ (not yet merged)
message(STATUS "Loading Zephyr BLE FreeRTOS variant for Pico W")

# Enable threading via FreeRTOS
set(MICROPY_PY_THREAD ON)

# Allocate 16KB C heap for GATT service/UUID allocations
set(MICROPY_C_HEAP_SIZE 16384)

# Override bluetooth stack selection
set(MICROPY_BLUETOOTH_BTSTACK OFF)
set(MICROPY_BLUETOOTH_ZEPHYR ON)

# Force fetch picotool from git (version mismatch workaround)
set(PICOTOOL_FORCE_FETCH_FROM_GIT ON)
