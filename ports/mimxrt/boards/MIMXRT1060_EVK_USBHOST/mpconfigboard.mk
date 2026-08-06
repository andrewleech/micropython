# MIMXRT1060-EVK with USB Host mode enabled.
# USB Host and USB Device are mutually exclusive on a single USB controller.

include $(BOARD_DIR)/../MIMXRT1060_EVK/mpconfigboard.mk

MICROPY_HW_USB_HOST = 1
MICROPY_HW_USB_CDC = 0
MICROPY_HW_USB_MSC = 0
