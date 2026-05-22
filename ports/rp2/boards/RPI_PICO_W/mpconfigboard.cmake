# cmake file for Raspberry Pi Pico W

set(PICO_BOARD "pico_w")

set(MICROPY_PY_LWIP ON)
set(MICROPY_PY_NETWORK_CYW43 ON)

# Bluetooth
set(MICROPY_PY_BLUETOOTH ON)
set(MICROPY_BLUETOOTH_BTSTACK ON)
set(MICROPY_PY_BLUETOOTH_CYW43 ON)

# Board specific version of the frozen manifest
set(MICROPY_FROZEN_MANIFEST ${MICROPY_BOARD_DIR}/manifest.py)

if(NOT DEFINED MICROPY_HW_FLASH_STORAGE_BYTES)
    set(MICROPY_HW_FLASH_STORAGE_BYTES 868352)  # 848 * 1024
endif()

# Opt the board into the mboot bootloader.  See RPI_PICO/mpconfigboard.cmake
# for an explanation of the USE_MBOOT flag.
set(USE_MBOOT 1)
add_compile_definitions(USE_MBOOT)
