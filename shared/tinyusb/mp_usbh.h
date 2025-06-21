/*
 * This file is part of the MicroPython project, http://micropython.org/
 *
 * The MIT License (MIT)
 *
 * Copyright (c) 2025 Andrew Leech
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
 */

#ifndef MICROPY_INCLUDED_SHARED_TINYUSB_MP_USBH_H
#define MICROPY_INCLUDED_SHARED_TINYUSB_MP_USBH_H

#include "py/obj.h"
#include "py/mpconfig.h"

#ifndef NO_QSTR
#include "tusb.h"
#include "device/dcd.h"
#endif

#if MICROPY_HW_USB_HOST

// Maximum number of pending exceptions per single TinyUSB task execution
#define MP_USBH_MAX_PEND_EXCS 2

// CDC IRQ trigger
#define USBH_CDC_IRQ_RX     1

// HID Constants
#define USBH_HID_MAX_REPORT_SIZE  64

// HID boot protocol codes
#define USBH_HID_PROTOCOL_NONE      0
#define USBH_HID_PROTOCOL_KEYBOARD  1
#define USBH_HID_PROTOCOL_MOUSE     2
#define USBH_HID_PROTOCOL_GENERIC   3

// HID report event types
#define USBH_HID_IRQ_REPORT      1

// Maximum number of device strings
#define USBH_MAX_DEVICES    (CFG_TUH_DEVICE_MAX)  // Maximum number of devices
#define USBH_MAX_STR_LEN    32

// Maximum number of CDC devices
#define USBH_MAX_CDC        4

// Maximum number of HID devices
#define USBH_MAX_HID        4

// Initialize TinyUSB for host mode
static inline void mp_usbh_init_tuh(void) {
    tuh_init(BOARD_TUH_RHPORT);
}

// Schedule a call to mp_usbd_task(), even if no USB interrupt has occurred
void mp_usbh_schedule_task(void);
void mp_usbh_task(void);

// MP_DECLARE_CONST_FUN_OBJ_1(usbh_cdc_irq_callback_obj);
MP_DECLARE_CONST_FUN_OBJ_1(usbh_hid_irq_callback_obj);

// The USBHost type
extern const mp_obj_type_t machine_usb_host_type;

// The USBH_Device type
extern const mp_obj_type_t machine_usbh_device_type;

// The USBH_CDC type
extern const mp_obj_type_t machine_usbh_cdc_type;

// The USBH_MSC type
extern const mp_obj_type_t machine_usbh_msc_type;

// The USBH_HID type
extern const mp_obj_type_t machine_usbh_hid_type;

// Structure to hold shared USB host state
typedef struct _mp_obj_usb_host_t {
    mp_obj_base_t base;

    bool initialized;                  // Whether TinyUSB host support is initialized
    bool active;                       // Whether USB host is active

    mp_obj_t device_list;              // List of connected general devices
    mp_obj_t cdc_list;                 // List of CDC devices
    mp_obj_t msc_list;                 // List of MSC devices
    mp_obj_t hid_list;                 // List of HID devices

    char manufacturer_str[USBH_MAX_DEVICES][USBH_MAX_STR_LEN]; // Manufacturer strings
    char product_str[USBH_MAX_DEVICES][USBH_MAX_STR_LEN];      // Product strings
    char serial_str[USBH_MAX_DEVICES][USBH_MAX_STR_LEN];       // Serial strings

    // Pointers to exceptions thrown inside Python callbacks. See usbh_callback_function_n()
    mp_uint_t num_pend_excs;                    // Number of pending exceptions
    mp_obj_t pend_excs[MP_USBH_MAX_PEND_EXCS];  // Pending exceptions to be processed
} mp_obj_usb_host_t;

// Structure to track USB device information
typedef struct _machine_usbh_device_obj_t {
    mp_obj_base_t base;
    uint8_t addr;               // Device address
    uint16_t vid;               // Vendor ID
    uint16_t pid;               // Product ID
    const char *manufacturer;   // Manufacturer string (may be NULL)
    const char *product;        // Product string (may be NULL)
    const char *serial;         // Serial number string (may be NULL)
    uint8_t dev_class;          // Device class
    uint8_t dev_subclass;       // Device subclass
    uint8_t dev_protocol;       // Device protocol
    bool mounted;               // Whether the device is currently mounted
} machine_usbh_device_obj_t;

// Structure for USB CDC device
typedef struct _machine_usbh_cdc_obj_t {
    mp_obj_base_t base;
    uint8_t dev_addr;           // Parent device
    bool connected;             // Whether device is connected
    uint8_t itf_num;            // Interface number
    mp_obj_t irq_callback;      // CDC IRQ callbacks
} machine_usbh_cdc_obj_t;

// Structure for USB MSC device (block device)
typedef struct _machine_usbh_msc_obj_t {
    mp_obj_base_t base;
    uint8_t dev_addr;           // Parent device
    bool connected;             // Whether device is connected
    uint8_t lun;                // Logical Unit Number
    uint32_t block_size;        // Block size in bytes
    uint32_t block_count;       // Number of blocks
    bool readonly;              // Whether the device is read-only
    uint8_t *block_cache;
    ssize_t block_cache_addr;
} machine_usbh_msc_obj_t;

// Structure for USB HID device
typedef struct _machine_usbh_hid_obj_t {
    mp_obj_base_t base;
    uint8_t dev_addr;           // Parent device
    bool connected;             // Whether device is connected
    uint8_t instance;           // HID instance (different from interface number)
    uint8_t protocol;           // HID protocol (KEYBOARD, MOUSE, GENERIC)
    uint16_t usage_page;        // HID usage page
    uint16_t usage;             // HID usage
    mp_obj_t latest_report;     // Last received report data
    mp_obj_t irq_callback;      // HID IRQ callbacks
} machine_usbh_hid_obj_t;

// Helper function to check if a device is mounted
bool device_mounted(uint8_t dev_addr);

// This macro can accept and of the device types (cdc, msc, hid) above.
#define DEVICE_ACTIVE(self) (self->connected && device_mounted(self->dev_addr))

#endif // MICROPY_HW_USB_HOST

#endif // MICROPY_INCLUDED_SHARED_TINYUSB_MP_USBH_H
