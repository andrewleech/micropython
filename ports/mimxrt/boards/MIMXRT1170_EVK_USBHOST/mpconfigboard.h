// MIMXRT1170-EVK with USB Host mode enabled.
// USB Host and USB Device are mutually exclusive on a single USB controller.

#ifndef MICROPY_HW_BOARD_MIMXRT1170_EVK_USBHOST
#define MICROPY_HW_BOARD_MIMXRT1170_EVK_USBHOST

// Include base board configuration.
#include "../MIMXRT1170_EVK/mpconfigboard.h"

// Override board name.
#undef MICROPY_HW_BOARD_NAME
#define MICROPY_HW_BOARD_NAME "i.MX RT1170 EVK (USB Host)"

// Disable USB device support (host and device are mutually exclusive on USB1).
#undef MICROPY_HW_ENABLE_USBDEV
#define MICROPY_HW_ENABLE_USBDEV (0)
#define MICROPY_HW_USB_CDC       (0)
#define MICROPY_HW_USB_MSC       (0)

#endif // MICROPY_HW_BOARD_MIMXRT1170_EVK_USBHOST
