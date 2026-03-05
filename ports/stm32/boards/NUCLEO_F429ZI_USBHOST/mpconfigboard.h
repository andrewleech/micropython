// NUCLEO-F429ZI with TinyUSB host mode enabled.
// Provides machine.USBHost for USB host operations.

// Include parent board configuration.
#include "boards/NUCLEO_F429ZI/mpconfigboard.h"

// Enable USB Host mode flag for conditional VBUS power config.
#ifndef MICROPY_HW_USB_HOST
#define MICROPY_HW_USB_HOST (1)
#endif

// USB Host VBUS configuration - override base board
#undef MICROPY_HW_USB_VBUS_DETECT_PIN
#define MICROPY_HW_USB_VBUS_POWER_PIN  (pin_G6)
#define MICROPY_HW_USB_VBUS_POWER_ON   (1)
