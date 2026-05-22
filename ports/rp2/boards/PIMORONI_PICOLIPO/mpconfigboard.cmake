# cmake file for Pimoroni Pico LiPo 4MB

# Opt the board into the mboot bootloader.  See RPI_PICO/mpconfigboard.cmake
# for an explanation of the USE_MBOOT flag.
set(USE_MBOOT 1)
add_compile_definitions(USE_MBOOT)
