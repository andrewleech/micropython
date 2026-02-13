// MIMXRT1170-EVK with dual-port USB: Device on OTG1, Host on OTG2.

#ifndef MICROPY_HW_BOARD_MIMXRT1170_EVK_USBHOST_DUAL
#define MICROPY_HW_BOARD_MIMXRT1170_EVK_USBHOST_DUAL

// Include base board configuration.
#include "../MIMXRT1170_EVK/mpconfigboard.h"

// Override board name.
#undef MICROPY_HW_BOARD_NAME
#define MICROPY_HW_BOARD_NAME "i.MX RT1170 EVK USB Host (Dual)"

// TinyUSB dual-port configuration.
// BOARD_TUD_RHPORT: USB Device on RHPORT0 (USB_OTG1).
// BOARD_TUH_RHPORT: USB Host on RHPORT1 (USB_OTG2).
#define BOARD_TUD_RHPORT         0
#define BOARD_TUH_RHPORT         1

#endif // MICROPY_HW_BOARD_MIMXRT1170_EVK_USBHOST_DUAL
