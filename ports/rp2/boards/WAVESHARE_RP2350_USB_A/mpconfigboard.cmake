# cmake file for Waveshare RP2350-USB-A

# The Waveshare RP2350-USB-A has 16MB flash
set(MICROPY_PY_BTREE 1)

# Set the board variant for the Pico SDK
set(PICO_BOARD "waveshare_rp2350_one")

# Enable USB Host support
set(MICROPY_HW_USB_HOST 1)