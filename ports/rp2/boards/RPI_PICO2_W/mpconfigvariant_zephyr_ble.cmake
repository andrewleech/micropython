# Zephyr BLE variant for Pico 2 W (cooperative polling, no FreeRTOS)
# Build with: make BOARD=RPI_PICO2_W BOARD_VARIANT=zephyr_ble

# RP2350 platform (normally set by the default mpconfigvariant.cmake)
set(PICO_PLATFORM "rp2350")

# Disable threading - use cooperative polling instead of FreeRTOS tasks
set(MICROPY_PY_THREAD OFF)

# Allocate 16KB C heap for GATT service/UUID allocations
set(MICROPY_C_HEAP_SIZE 16384)

# Override bluetooth stack selection
set(MICROPY_BLUETOOTH_BTSTACK OFF)
set(MICROPY_BLUETOOTH_ZEPHYR ON)

# Force fetch picotool from git (version mismatch workaround)
set(PICOTOOL_FORCE_FETCH_FROM_GIT ON)
