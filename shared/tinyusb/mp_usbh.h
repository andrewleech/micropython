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
#include "py/runtime.h"

#if MICROPY_HW_USB_HOST

// Initialize TinyUSB for host mode
static inline void mp_usbh_init_tuh(void) {
    tusb_init();
}

// Function to be called in the port's main loop to process USB host events
void mp_usbh_task(void);

// The USBHost type
extern const mp_obj_type_t machine_usb_host_type;

// The USBH_CDC type
extern const mp_obj_type_t machine_usbh_cdc_type;

// The USBH_MSC type
extern const mp_obj_type_t machine_usbh_msc_type;

// Initialize USB Host module at boot
void machine_usbh_init0(void);

// Structure to track USB device information
typedef struct _machine_usbh_device_obj_t {
    mp_obj_base_t base;
    uint8_t addr;            // Device address
    uint16_t vid;            // Vendor ID
    uint16_t pid;            // Product ID
    const char *manufacturer; // Manufacturer string (may be NULL)
    const char *product;     // Product string (may be NULL)
    const char *serial;      // Serial number string (may be NULL)
    uint8_t dev_class;       // Device class
    uint8_t dev_subclass;    // Device subclass
    uint8_t dev_protocol;    // Device protocol
    bool mounted;            // Whether the device is currently mounted
} machine_usbh_device_obj_t;

// Structure for USB CDC device
typedef struct _machine_usbh_cdc_obj_t {
    mp_obj_base_t base;
    machine_usbh_device_obj_t *device; // Parent device
    uint8_t itf_num;                 // Interface number
    bool connected;                  // Whether device is connected
    bool rx_pending;                 // Whether there's data available to read
} machine_usbh_cdc_obj_t;

// Structure for USB MSC device (block device)
typedef struct _machine_usbh_msc_obj_t {
    mp_obj_base_t base;
    machine_usbh_device_obj_t *device; // Parent device
    uint8_t lun;                     // Logical Unit Number
    bool connected;                  // Whether device is connected
    uint32_t block_size;             // Block size in bytes
    uint32_t block_count;            // Number of blocks
    bool readonly;                   // Whether the device is read-only
} machine_usbh_msc_obj_t;

#endif // MICROPY_HW_USB_HOST

#endif // MICROPY_INCLUDED_SHARED_TINYUSB_MP_USBH_H
