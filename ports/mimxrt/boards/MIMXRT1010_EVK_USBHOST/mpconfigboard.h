// MIMXRT1010-EVK with USB Host mode enabled.
// RT1010 has limited DTCM (32KB) so USB Host data is placed in OCRAM.
// TinyUSB config is reduced: max 2 devices, no hub support.

#ifndef MICROPY_HW_BOARD_MIMXRT1010_EVK_USBHOST
#define MICROPY_HW_BOARD_MIMXRT1010_EVK_USBHOST

// Include base board configuration.
#include "../MIMXRT1010_EVK/mpconfigboard.h"

// Override board name.
#undef MICROPY_HW_BOARD_NAME
#define MICROPY_HW_BOARD_NAME "i.MX RT1010 EVK (USB Host)"

// Disable USB device support (host and device are mutually exclusive).
#undef MICROPY_HW_ENABLE_USBDEV
#define MICROPY_HW_ENABLE_USBDEV (0)
#define MICROPY_HW_USB_CDC       (0)
#define MICROPY_HW_USB_MSC       (0)

// RT1010 USB Host: Reduced TinyUSB config to fit in available memory.
#define CFG_TUH_DEVICE_MAX       2
#define CFG_TUH_ENDPOINT_MAX     8
#define CFG_TUH_HUB              0
#define CFG_TUH_CDC              2
#define CFG_TUH_MSC              1
#define CFG_TUH_HID              2

// Place TinyUSB host data in OCRAM instead of DTCM.
#define CFG_TUH_MEM_SECTION      __attribute__((section(".usb_host_ram")))

#endif // MICROPY_HW_BOARD_MIMXRT1010_EVK_USBHOST
