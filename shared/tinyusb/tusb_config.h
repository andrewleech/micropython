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

#ifndef CFG_TUSB_RHPORT0_MODE
#define CFG_TUSB_RHPORT0_MODE   (OPT_MODE_DEVICE)
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

#define USBD_RHPORT (0) // Currently only one port is supported

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

#if MICROPY_HW_USB_HOST


// Enable Host stack
#define CFG_TUH_ENABLED       1

#if CFG_TUSB_MCU == OPT_MCU_RP2040
// #define CFG_TUH_RPI_PIO_USB   1 // use pio-usb as host controller
// #define CFG_TUH_MAX3421       1 // use max3421 as host controller

// host roothub port is 1 if using either pio-usb or max3421
  #if (defined(CFG_TUH_RPI_PIO_USB) && CFG_TUH_RPI_PIO_USB) || (defined(CFG_TUH_MAX3421) && CFG_TUH_MAX3421)
    #define BOARD_TUH_RHPORT      1
  #endif
#endif

// Default is max speed that hardware controller could support with on-chip PHY
#define CFG_TUH_MAX_SPEED     BOARD_TUH_MAX_SPEED

// ------------------------- Board Specific --------------------------

// RHPort number used for host can be defined by board.mk, default to port 0
#ifndef BOARD_TUH_RHPORT
#define BOARD_TUH_RHPORT      0
#endif

// RHPort max operational speed can defined by board.mk
#ifndef BOARD_TUH_MAX_SPEED
#define BOARD_TUH_MAX_SPEED   OPT_MODE_DEFAULT_SPEED
#endif

// --------------------------------------------------------------------
// Driver Configuration
// --------------------------------------------------------------------

// Size of buffer to hold descriptors and other data used for enumeration
#define CFG_TUH_ENUMERATION_BUFSIZE 256

#define CFG_TUH_HUB                 1 // number of supported hubs

#if MICROPY_PY_USBIP // USBIP module takes over standard classes
#define CFG_TUH_CDC                 0
#define CFG_TUH_MSC                 0
#define CFG_TUH_HID                 0 // USBIP will handle HID devices if needed
#define CFG_TUH_APPLICATION_DRIVER  1 // Enable the application driver hook
#else // Default configuration: Enable standard host classes
#define CFG_TUH_CDC                 1 // CDC ACM
#define CFG_TUH_MSC                 1
#define CFG_TUH_HID                 (3 * CFG_TUH_DEVICE_MAX) // typical keyboard + mouse device can have 3-4 HID interfaces
#define CFG_TUH_APPLICATION_DRIVER  0 // Disable the application driver hook by default
#endif

#define CFG_TUH_CDC_FTDI            1 // FTDI Serial.  FTDI is not part of CDC class, only to reuse CDC driver API
#define CFG_TUH_CDC_CP210X          1 // CP210x Serial. CP210X is not part of CDC class, only to reuse CDC driver API
#define CFG_TUH_CDC_CH34X           1 // CH340 or CH341 Serial. CH34X is not part of CDC class, only to reuse CDC driver API
#define CFG_TUH_VENDOR              0

// max device support (excluding hub device): 1 hub typically has 4 ports
#define CFG_TUH_DEVICE_MAX          (3 * CFG_TUH_HUB + 1)

// ------------- HID -------------//
#define CFG_TUH_HID_EPIN_BUFSIZE    64
#define CFG_TUH_HID_EPOUT_BUFSIZE   64

// ------------- CDC -------------//

// Set Line Control state on enumeration/mounted:
// DTR ( bit 0), RTS (bit 1)
#define CFG_TUH_CDC_LINE_CONTROL_ON_ENUM    0x03

// Set Line Coding on enumeration/mounted, value for cdc_line_coding_t
// bit rate = 115200, 1 stop bit, no parity, 8 bit data width
#define CFG_TUH_CDC_LINE_CODING_ON_ENUM   { 115200, CDC_LINE_CODING_STOP_BITS_1, CDC_LINE_CODING_PARITY_NONE, 8 }

#endif // MICROPY_HW_USB_HOST

#endif // MICROPY_INCLUDED_SHARED_TINYUSB_TUSB_CONFIG_H
