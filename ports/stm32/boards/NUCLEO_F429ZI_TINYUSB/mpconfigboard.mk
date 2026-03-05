include boards/NUCLEO_F429ZI/mpconfigboard.mk

# Enable TinyUSB stack for USB Device mode.
# This provides machine.USBDevice for runtime USB device configuration.
MICROPY_HW_TINYUSB_STACK = 1
