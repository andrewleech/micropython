# Include base MIMXRT1170_EVK board configuration.
include $(BOARD_DIR)/../MIMXRT1170_EVK/mpconfigboard.mk

# Enable USB Host on USB2 (USB_OTG2).
MICROPY_HW_USB_HOST = 1

# Keep USB Device CDC enabled on USB1 (USB_OTG1) for REPL.
MICROPY_HW_USB_CDC = 1
