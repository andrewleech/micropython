include $(BOARD_DIR)/../MIMXRT1010_EVK/mpconfigboard.mk

MICROPY_HW_USB_HOST = 1
MICROPY_HW_USB_CDC = 0
MICROPY_HW_USB_MSC = 0

# Use custom linker script that adjusts GC heap to start after USB host RAM in OCRAM.
LD_FILES = boards/MIMXRT1011_usbhost.ld boards/common.ld
