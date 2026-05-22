# cmake file for Raspberry Pi Pico2
set(PICO_BOARD "pico2")

# To change the gpio count for QFN-80
# set(PICO_NUM_GPIOS 48)

if(NOT DEFINED MICROPY_HW_FLASH_STORAGE_BYTES)
    set(MICROPY_HW_FLASH_STORAGE_BYTES 3145728)  # 4MB flash - 1MB firmware = 3MB filesystem
endif()

# Opt the board into the mboot bootloader.  See RPI_PICO/mpconfigboard.cmake
# for an explanation of the USE_MBOOT flag.
set(USE_MBOOT 1)
add_compile_definitions(USE_MBOOT)
