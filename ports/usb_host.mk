# USB Host support makefile fragment
# This file provides common USB Host build configuration for ports

ifeq ($(MICROPY_HW_USB_HOST),1)

# Include USB Host source files
SRC_C += \
	shared/tinyusb/mp_usbh.c \
	extmod/machine_usb_host.c

# Include TinyUSB host files
TINYUSB_HOST_ENABLE = 1

# Add USB Host specific defines
CFLAGS += -DMICROPY_HW_USB_HOST=1

# USB Host class support
ifeq ($(MICROPY_HW_USB_HOST_CDC),1)
CFLAGS += -DMICROPY_HW_USB_HOST_CDC=1
endif

ifeq ($(MICROPY_HW_USB_HOST_MSC),1)
CFLAGS += -DMICROPY_HW_USB_HOST_MSC=1
endif

ifeq ($(MICROPY_HW_USB_HOST_HID),1)
CFLAGS += -DMICROPY_HW_USB_HOST_HID=1
endif

# Include path for USB Host headers
INC += -I$(TOP)/shared/tinyusb

endif