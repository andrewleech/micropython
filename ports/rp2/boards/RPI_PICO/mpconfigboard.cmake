# cmake file for Raspberry Pi Pico
set(PICO_BOARD "pico")
set(PICO_PLATFORM "rp2040")

if(NOT DEFINED MICROPY_HW_FLASH_STORAGE_BYTES)
    set(MICROPY_HW_FLASH_STORAGE_BYTES 1441792)  # 1408 * 1024
endif()

# Opt the board into the mboot bootloader.  USE_MBOOT propagates as a -D so
# both ports/rp2/mpconfigport.h and the per-board mpconfigboard.h can guard
# mboot-specific code with `#if defined(USE_MBOOT)`.  The linker script
# selection is centralised in ports/rp2/CMakeLists.txt.
set(USE_MBOOT 1)
add_compile_definitions(USE_MBOOT)
