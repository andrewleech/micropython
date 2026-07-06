# Include base RPI_PICO2 config
include(${CMAKE_CURRENT_LIST_DIR}/../RPI_PICO2/mpconfigboard.cmake)

# Enable USB Host support
set(MICROPY_HW_USB_HOST 1)
