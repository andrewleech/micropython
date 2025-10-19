/*
 * The MIT License (MIT)
 *
 * Copyright (c) 2020-2021 Damien P. George
 * Copyright (c) 2022 Angus Gratton
 *
 * Permission is hereby granted, free of charge, to any person obtaining a copy
 * of this software and associated documentation files (the "Software"), to deal
 * in the Software without restriction, including without limitation the rights
 * to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
 * copies of the Software, and to permit persons to whom the Software is
 * furnished to do so, subject to the following conditions:
 *
 * The above copyright notice and this permission notice shall be included in
 * all copies or substantial portions of the Software.
 *
 * THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
 * IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
 * FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
 * AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
 * LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
 * OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
 * THE SOFTWARE.
 *
 */

#ifndef MICROPY_INCLUDED_SHARED_TINYUSB_TUSB_CONFIG_H
#define MICROPY_INCLUDED_SHARED_TINYUSB_TUSB_CONFIG_H

#include "py/mpconfig.h"

#if MICROPY_HW_ENABLE_USBDEV

#ifndef MICROPY_HW_ENABLE_USB_RUNTIME_DEVICE
#define MICROPY_HW_ENABLE_USB_RUNTIME_DEVICE 0
#endif

#ifndef MICROPY_HW_USB_MANUFACTURER_STRING
#define MICROPY_HW_USB_MANUFACTURER_STRING "MicroPython"
#endif

#ifndef MICROPY_HW_USB_PRODUCT_FS_STRING
#define MICROPY_HW_USB_PRODUCT_FS_STRING "Board in FS mode"
#endif

#ifndef MICROPY_HW_USB_CDC_INTERFACE_STRING
#define MICROPY_HW_USB_CDC_INTERFACE_STRING "Board CDC"
#endif

#ifndef MICROPY_HW_USB_MSC_INQUIRY_VENDOR_STRING
#define MICROPY_HW_USB_MSC_INQUIRY_VENDOR_STRING "MicroPy"
#endif

#ifndef MICROPY_HW_USB_MSC_INQUIRY_PRODUCT_STRING
#define MICROPY_HW_USB_MSC_INQUIRY_PRODUCT_STRING "Mass Storage"
#endif

#ifndef MICROPY_HW_USB_MSC_INQUIRY_REVISION_STRING
#define MICROPY_HW_USB_MSC_INQUIRY_REVISION_STRING "1.00"
#endif

// Configure RHPORT modes based on board USB configuration
// Note: MICROPY_HW_TINYUSB_RHPORTx_MODE from mpconfigboard_common.h is NOT available here
// because tusb_config.h is included by TinyUSB's build before mpconfigport.h.
// So we replicate the RHPORT selection logic using the board USB macros that ARE available.

#ifndef CFG_TUSB_RHPORT0_MODE
#if defined(MICROPY_HW_USB_MAIN_DEV) && (MICROPY_HW_USB_MAIN_DEV == USB_PHY_HS_ID)
// High-Speed controller selected - disable RHPORT0 (FS controller)
  #if defined(STM32F4) || defined(STM32F7) || defined(STM32H7)
// These families have separate HS and FS controllers
    #define CFG_TUSB_RHPORT0_MODE   (OPT_MODE_NONE)
  #else
// Other families: HS PHY on same controller as FS
    #define CFG_TUSB_RHPORT0_MODE   (OPT_MODE_DEVICE | OPT_MODE_FULL_SPEED)
  #endif
#else
// Full-Speed controller selected (or auto-detect) - enable RHPORT0
  #define CFG_TUSB_RHPORT0_MODE   (OPT_MODE_DEVICE | OPT_MODE_FULL_SPEED)
#endif
#endif

#ifndef CFG_TUSB_RHPORT1_MODE
#if defined(MICROPY_HW_USB_MAIN_DEV) && (MICROPY_HW_USB_MAIN_DEV == USB_PHY_HS_ID)
// High-Speed controller selected - configure RHPORT1 mode
  #if defined(STM32F4) || defined(STM32F7) || defined(STM32H7)
// These families have separate HS and FS controllers
    #if defined(MICROPY_HW_USB_HS_ULPI)
// External ULPI PHY - use High-Speed
      #define CFG_TUSB_RHPORT1_MODE   (OPT_MODE_DEVICE | OPT_MODE_HIGH_SPEED)
    #else
// Internal PHY or HS-in-FS mode - use Full-Speed
      #define CFG_TUSB_RHPORT1_MODE   (OPT_MODE_DEVICE | OPT_MODE_FULL_SPEED)
    #endif
  #else
// Other families: HS PHY on same controller as FS - disable RHPORT1
    #define CFG_TUSB_RHPORT1_MODE   (OPT_MODE_NONE)
  #endif
#else
// Full-Speed controller selected (or auto-detect) - disable RHPORT1
  #define CFG_TUSB_RHPORT1_MODE   (OPT_MODE_NONE)
#endif
#endif

#if MICROPY_HW_USB_CDC
#define CFG_TUD_CDC             (1)
#else
#define CFG_TUD_CDC             (0)
#endif

#if MICROPY_HW_USB_MSC
#define CFG_TUD_MSC             (1)
#else
#define CFG_TUD_MSC             (0)
#endif

// CDC Configuration
#if CFG_TUD_CDC
#ifndef CFG_TUD_CDC_RX_BUFSIZE
#define CFG_TUD_CDC_RX_BUFSIZE  ((CFG_TUD_MAX_SPEED == OPT_MODE_HIGH_SPEED) ? 512 : 256)
#endif
#ifndef CFG_TUD_CDC_TX_BUFSIZE
#define CFG_TUD_CDC_TX_BUFSIZE  ((CFG_TUD_MAX_SPEED == OPT_MODE_HIGH_SPEED) ? 512 : 256)
#endif
#endif

// MSC Configuration
#if CFG_TUD_MSC
#ifndef MICROPY_HW_USB_MSC_INTERFACE_STRING
#define MICROPY_HW_USB_MSC_INTERFACE_STRING "Board MSC"
#endif
// Set MSC EP buffer size to FatFS block size to avoid partial read/writes (offset arg).
#define CFG_TUD_MSC_BUFSIZE (MICROPY_FATFS_MAX_SS)
#endif

// Board-configurable RHPORT selection
#ifndef MICROPY_HW_TINYUSB_RHPORT
#define MICROPY_HW_TINYUSB_RHPORT (0) // Default to port 0
#endif

#define USBD_RHPORT MICROPY_HW_TINYUSB_RHPORT

// Define built-in interface, string and endpoint numbering based on the above config

#define USBD_STR_0 (0x00)
#define USBD_STR_MANUF (0x01)
#define USBD_STR_PRODUCT (0x02)
#define USBD_STR_SERIAL (0x03)
#define USBD_STR_CDC (0x04)
#define USBD_STR_MSC (0x05)

#define USBD_MAX_POWER_MA (250)

#ifndef MICROPY_HW_USB_DESC_STR_MAX
#define MICROPY_HW_USB_DESC_STR_MAX (40)
#endif

#if CFG_TUD_CDC
#define USBD_ITF_CDC (0) // needs 2 interfaces
#define USBD_CDC_EP_CMD (0x81)
#define USBD_CDC_EP_OUT (0x02)
#define USBD_CDC_EP_IN (0x82)
#endif // CFG_TUD_CDC

#if CFG_TUD_MSC
// Interface & Endpoint numbers for MSC come after CDC, if it is enabled
#if CFG_TUD_CDC
#define USBD_ITF_MSC (2)
#define EPNUM_MSC_OUT (0x03)
#define EPNUM_MSC_IN (0x83)
#else
#define USBD_ITF_MSC (0)
#define EPNUM_MSC_OUT (0x01)
#define EPNUM_MSC_IN (0x81)
#endif // CFG_TUD_CDC
#endif // CFG_TUD_MSC

/* Limits of builtin USB interfaces, endpoints, strings */
#if CFG_TUD_MSC
#define USBD_ITF_BUILTIN_MAX (USBD_ITF_MSC + 1)
#define USBD_STR_BUILTIN_MAX (USBD_STR_MSC + 1)
#define USBD_EP_BUILTIN_MAX (EPNUM_MSC_OUT + 1)
#elif CFG_TUD_CDC
#define USBD_ITF_BUILTIN_MAX (USBD_ITF_CDC + 2)
#define USBD_STR_BUILTIN_MAX (USBD_STR_CDC + 1)
#define USBD_EP_BUILTIN_MAX (((USBD_CDC_EP_IN)&~TUSB_DIR_IN_MASK) + 1)
#else // !CFG_TUD_MSC && !CFG_TUD_CDC
#define USBD_ITF_BUILTIN_MAX (0)
#define USBD_STR_BUILTIN_MAX (0)
#define USBD_EP_BUILTIN_MAX (0)
#endif

#endif // MICROPY_HW_ENABLE_USBDEV

#endif // MICROPY_INCLUDED_SHARED_TINYUSB_TUSB_CONFIG_H
