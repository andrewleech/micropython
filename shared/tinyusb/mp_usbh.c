/*
 * This file is part of the MicroPython project, http://micropython.org/
 *
 * The MIT License (MIT)
 *
 * Copyright (c) 2025 Claude AI Assistant
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

#include <stdlib.h>
#include <string.h>

#include "py/mphal.h"
#include "py/obj.h"
#include "py/objstr.h"
#include "py/runtime.h"
#include "py/mperrno.h"
#include "py/stream.h"
#include "extmod/vfs.h"
#include "shared/runtime/mpirq.h"

#include "mp_usbd.h"
#include "mp_usbh.h"

#if MICROPY_HW_USB_HOST || 1

#ifndef NO_QSTR
#include "tusb.h"
#include "host/usbh.h"
#include "class/cdc/cdc_host.h"
#include "class/msc/msc_host.h"
#include "class/hid/hid_host.h"
#endif

// Max number of devices we can keep track of
#define USBH_MAX_DEVICES (CFG_TUH_DEVICE_MAX)

// Max number of CDC devices we can keep track of
#define USBH_MAX_CDC (CFG_TUH_CDC)

// Max number of MSC devices we can keep track of
#define USBH_MAX_MSC (CFG_TUH_MSC)

// Max number of HID devices we can keep track of
#define USBH_MAX_HID (CFG_TUH_HID)

// Storage for string descriptors
#define MAX_STRING_LEN 32

// Forward declarations
static void usbh_device_mount_cb(uint8_t dev_addr);
static void usbh_device_unmount_cb(uint8_t dev_addr);
static void usbh_cdc_mount_cb(uint8_t itf_num);
static void usbh_cdc_unmount_cb(uint8_t itf_num);
static void usbh_cdc_rx_cb(uint8_t itf_num);
static void usbh_msc_mount_cb(uint8_t dev_addr);
static void usbh_msc_unmount_cb(uint8_t dev_addr);
static void usbh_hid_mount_cb(uint8_t dev_addr, uint8_t instance, uint8_t const *report_desc, uint16_t desc_len);
static void usbh_hid_unmount_cb(uint8_t dev_addr, uint8_t instance);
static void usbh_hid_report_received_cb(uint8_t dev_addr, uint8_t instance, uint8_t const *report, uint16_t len);

// Global state for USB Host
typedef struct _machine_usb_host_obj_t {
    mp_obj_list_t *device_list;   // List of detected USB devices
    mp_obj_list_t *cdc_list;      // List of detected CDC devices
    mp_obj_list_t *msc_list;      // List of detected MSC devices
    mp_obj_list_t *hid_list;      // List of detected HID devices
    bool initialized;             // Whether USB Host is initialized
    bool active;                  // Whether USB Host is active

    // Storage for string descriptors
    char manufacturer_str[USBH_MAX_DEVICES][MAX_STRING_LEN];
    char product_str[USBH_MAX_DEVICES][MAX_STRING_LEN];
    char serial_str[USBH_MAX_DEVICES][MAX_STRING_LEN];
} machine_usb_host_obj_t;

static machine_usb_host_obj_t usbh_state;

// CDC device IRQ (for callbacks when data is received)
static mp_obj_t usbh_cdc_irq_callback[USBH_MAX_CDC] = {mp_const_none};
static bool usbh_cdc_irq_scheduled[USBH_MAX_CDC] = {false};

// HID device IRQ (for callbacks when reports are received)
static mp_obj_t usbh_hid_irq_callback[USBH_MAX_HID] = {mp_const_none};
static bool usbh_hid_irq_scheduled[USBH_MAX_HID] = {false};

// Constants for USBH_CDC.irq trigger
#define USBH_CDC_IRQ_RX (1)

// Helper function to find a USB device by address
static machine_usbh_device_obj_t *find_device_by_addr(uint8_t addr) {
    for (size_t i = 0; i < usbh_state.device_list->len; i++) {
        machine_usbh_device_obj_t *device = MP_OBJ_TO_PTR(usbh_state.device_list->items[i]);
        if (device->addr == addr) {
            return device;
        }
    }
    return NULL;
}

// Helper function to find a CDC device by address and interface
static machine_usbh_cdc_obj_t *find_cdc_by_itf(uint8_t itf_num) {
    for (size_t i = 0; i < usbh_state.cdc_list->len; i++) {
        machine_usbh_cdc_obj_t *cdc = MP_OBJ_TO_PTR(usbh_state.cdc_list->items[i]);
        if (cdc->itf_num == itf_num) {
            return cdc;
        }
    }
    return NULL;
}

// Helper function to find a MSC device by address and LUN
static machine_usbh_msc_obj_t *find_msc_by_addr_lun(uint8_t addr, uint8_t lun) {
    for (size_t i = 0; i < usbh_state.msc_list->len; i++) {
        machine_usbh_msc_obj_t *msc = MP_OBJ_TO_PTR(usbh_state.msc_list->items[i]);
        if (msc->device->addr == addr && msc->lun == lun) {
            return msc;
        }
    }
    return NULL;
}

// Helper function to find a HID device by address and instance
static machine_usbh_hid_obj_t *find_hid_by_addr_instance(uint8_t addr, uint8_t instance) {
    for (size_t i = 0; i < usbh_state.hid_list->len; i++) {
        machine_usbh_hid_obj_t *hid = MP_OBJ_TO_PTR(usbh_state.hid_list->items[i]);
        if (hid->device->addr == addr && hid->instance == instance) {
            return hid;
        }
    }
    return NULL;
}

// Function to initialize the USB Host module
void machine_usbh_init0(void) {
    // Create lists to track devices
    usbh_state.device_list = mp_obj_new_list(0, NULL);
    usbh_state.cdc_list = mp_obj_new_list(0, NULL);
    usbh_state.msc_list = mp_obj_new_list(0, NULL);
    usbh_state.hid_list = mp_obj_new_list(0, NULL);
    usbh_state.initialized = false;
    usbh_state.active = false;

    // Initialize string storage
    for (int i = 0; i < USBH_MAX_DEVICES; i++) {
        usbh_state.manufacturer_str[i][0] = '\0';
        usbh_state.product_str[i][0] = '\0';
        usbh_state.serial_str[i][0] = '\0';
    }

    // Register CDC IRQ callbacks
    for (int i = 0; i < USBH_MAX_CDC; i++) {
        usbh_cdc_irq_callback[i] = mp_const_none;
        usbh_cdc_irq_scheduled[i] = false;
    }

    // Register HID IRQ callbacks
    for (int i = 0; i < USBH_MAX_HID; i++) {
        usbh_hid_irq_callback[i] = mp_const_none;
        usbh_hid_irq_scheduled[i] = false;
    }
}

// Task function that should be called regularly to process USB events
void mp_usbh_task(void) {
    if (usbh_state.initialized && usbh_state.active) {
        tuh_task();
    }
}

// Callback when a USB device is mounted
static void usbh_device_mount_cb(uint8_t dev_addr) {
    // Get device information
    tusb_desc_device_t desc_device;
    uint8_t xfer_result = tuh_descriptor_get_device_sync(dev_addr, &desc_device, sizeof(desc_device));
    if (XFER_RESULT_SUCCESS != xfer_result) {
        // printf("Failed to get device descriptor\r\n");
        return;
    }

    // Create a new USB device object
    machine_usbh_device_obj_t *device = mp_obj_malloc(machine_usbh_device_obj_t, &machine_usb_host_type);
    device->addr = dev_addr;
    device->vid = desc_device.idVendor;
    device->pid = desc_device.idProduct;
    device->dev_class = desc_device.bDeviceClass;
    device->dev_subclass = desc_device.bDeviceSubClass;
    device->dev_protocol = desc_device.bDeviceProtocol;
    device->mounted = true;

    // Get string descriptors using direct synchronous functions (these are optional)
    uint16_t temp_buf[MAX_STRING_LEN];
    const uint16_t language_id = 0x0409; // English US

    // Get manufacturer string
    if (desc_device.iManufacturer) {
        xfer_result = tuh_descriptor_get_manufacturer_string_sync(dev_addr, language_id, temp_buf, sizeof(temp_buf));
        if (xfer_result == XFER_RESULT_SUCCESS) {
            // Convert UTF-16LE to UTF-8 (simplified)
            for (size_t i = 1; i < temp_buf[0] / 2 && i < MAX_STRING_LEN - 1; i++) {
                usbh_state.manufacturer_str[dev_addr - 1][i - 1] = (char)(temp_buf[i] & 0xFF);
            }
            usbh_state.manufacturer_str[dev_addr - 1][MIN(temp_buf[0] / 2 - 1, MAX_STRING_LEN - 1)] = '\0';
            device->manufacturer = usbh_state.manufacturer_str[dev_addr - 1];
        } else {
            device->manufacturer = NULL;
        }
    } else {
        device->manufacturer = NULL;
    }

    // Get product string
    if (desc_device.iProduct) {
        xfer_result = tuh_descriptor_get_product_string_sync(dev_addr, language_id, temp_buf, sizeof(temp_buf));
        if (xfer_result == XFER_RESULT_SUCCESS) {
            // Convert UTF-16LE to UTF-8 (simplified)
            for (size_t i = 1; i < temp_buf[0] / 2 && i < MAX_STRING_LEN - 1; i++) {
                usbh_state.product_str[dev_addr - 1][i - 1] = (char)(temp_buf[i] & 0xFF);
            }
            usbh_state.product_str[dev_addr - 1][MIN(temp_buf[0] / 2 - 1, MAX_STRING_LEN - 1)] = '\0';
            device->product = usbh_state.product_str[dev_addr - 1];
        } else {
            device->product = NULL;
        }
    } else {
        device->product = NULL;
    }

    // Get serial number string
    if (desc_device.iSerialNumber) {
        xfer_result = tuh_descriptor_get_serial_string_sync(dev_addr, language_id, temp_buf, sizeof(temp_buf));
        if (xfer_result == XFER_RESULT_SUCCESS) {
            // Convert UTF-16LE to UTF-8 (simplified)
            for (size_t i = 1; i < temp_buf[0] / 2 && i < MAX_STRING_LEN - 1; i++) {
                usbh_state.serial_str[dev_addr - 1][i - 1] = (char)(temp_buf[i] & 0xFF);
            }
            usbh_state.serial_str[dev_addr - 1][MIN(temp_buf[0] / 2 - 1, MAX_STRING_LEN - 1)] = '\0';
            device->serial = usbh_state.serial_str[dev_addr - 1];
        } else {
            device->serial = NULL;
        }
    } else {
        device->serial = NULL;
    }

    // Add to the device list
    mp_obj_list_append(usbh_state.device_list, MP_OBJ_FROM_PTR(device));

    // // Open interfaces based on device class
    // if (device->dev_class == TUSB_CLASS_MISC &&
    //     device->dev_subclass == MISC_SUBCLASS_COMMON &&
    //     device->dev_protocol == MISC_PROTOCOL_IAD) {
    //     // Device uses Interface Association Descriptor (IAD) - check interfaces
    //     // tuh_cdc_itf_mount(dev_addr, 0);
    //     // tuh_msc_mount(dev_addr, 0);
    // } else if (device->dev_class == TUSB_CLASS_CDC) {
    //     // CDC device
    //     tuh_cdc_mount(dev_addr, 0);
    // } else if (device->dev_class == TUSB_CLASS_MSC) {
    //     // MSC device
    //     tuh_msc_mount(dev_addr, 0);
    // }
}

// Callback when a USB device is unmounted
static void usbh_device_unmount_cb(uint8_t dev_addr) {
    // Find the device in our list
    machine_usbh_device_obj_t *device = find_device_by_addr(dev_addr);
    if (device) {
        device->mounted = false;

        // Remove any CDC devices associated with this address
        for (int i = usbh_state.cdc_list->len - 1; i >= 0; i--) {
            machine_usbh_cdc_obj_t *cdc = MP_OBJ_TO_PTR(usbh_state.cdc_list->items[i]);
            if (cdc->device->addr == dev_addr) {
                mp_obj_list_remove(usbh_state.cdc_list, MP_OBJ_FROM_PTR(cdc));
            }
        }

        // Remove any MSC devices associated with this address
        for (int i = usbh_state.msc_list->len - 1; i >= 0; i--) {
            machine_usbh_msc_obj_t *msc = MP_OBJ_TO_PTR(usbh_state.msc_list->items[i]);
            if (msc->device->addr == dev_addr) {
                mp_obj_list_remove(usbh_state.msc_list, MP_OBJ_FROM_PTR(msc));
            }
        }

        // Remove any HID devices associated with this address
        for (int i = usbh_state.hid_list->len - 1; i >= 0; i--) {
            machine_usbh_hid_obj_t *hid = MP_OBJ_TO_PTR(usbh_state.hid_list->items[i]);
            if (hid->device->addr == dev_addr) {
                mp_obj_list_remove(usbh_state.hid_list, MP_OBJ_FROM_PTR(hid));
            }
        }

        // Remove the device from the list
        for (int i = 0; i < usbh_state.device_list->len; i++) {
            if (usbh_state.device_list->items[i] == MP_OBJ_FROM_PTR(device)) {
                mp_obj_list_remove(usbh_state.device_list, MP_OBJ_FROM_PTR(device));
                break;
            }
        }
    }
}

// Callback when a CDC interface is mounted
static void usbh_cdc_mount_cb(uint8_t itf_num) {
    tuh_itf_info_t itf_info = {0};
    tuh_cdc_itf_get_info(itf_num, &itf_info);

    // Find the device
    machine_usbh_device_obj_t *device = find_device_by_addr(itf_info.daddr);
    if (!device) {
        return;
    }

    // Create a CDC device object
    machine_usbh_cdc_obj_t *cdc = mp_obj_malloc(machine_usbh_cdc_obj_t, &machine_usbh_cdc_type);
    cdc->device = device;
    cdc->itf_num = itf_num;
    cdc->connected = true;
    cdc->rx_pending = false;

    // Add to CDC list
    mp_obj_list_append(usbh_state.cdc_list, MP_OBJ_FROM_PTR(cdc));

    // Set line coding
    cdc_line_coding_t line_coding = { 115200, CDC_LINE_CODING_STOP_BITS_1, CDC_LINE_CODING_PARITY_NONE, 8 };
    tuh_cdc_set_line_coding(itf_num, &line_coding, NULL, 0);
}

// Callback when a CDC interface is unmounted
static void usbh_cdc_unmount_cb(uint8_t itf_num) {
    // Find and remove any CDC devices with this address
    for (int i = usbh_state.cdc_list->len - 1; i >= 0; i--) {
        machine_usbh_cdc_obj_t *cdc = MP_OBJ_TO_PTR(usbh_state.cdc_list->items[i]);
        if (cdc->itf_num == itf_num) {
            cdc->connected = false;
            mp_obj_list_remove(usbh_state.cdc_list, MP_OBJ_FROM_PTR(cdc));
        }
    }
}


// Callback after MSC device has been scanned
static scsi_inquiry_resp_t inquiry_resp;
static bool usbh_msc_inquiry_complete_cb(uint8_t dev_addr, tuh_msc_complete_data_t const *cb_data) {
    msc_cbw_t const *cbw = cb_data->cbw;
    msc_csw_t const *csw = cb_data->csw;

    if (csw->status != 0) {
        // printf("Inquiry failed\r\n");
        return false;
    }

    machine_usbh_msc_obj_t *msc = (machine_usbh_msc_obj_t *)(cb_data->user_arg);

    // Print out Vendor ID, Product ID and Rev
    // printf("%.8s %.16s rev %.4s\r\n", inquiry_resp.vendor_id, inquiry_resp.product_id, inquiry_resp.product_rev);

    // Get capacity of device
    msc->block_count = tuh_msc_get_block_count(dev_addr, cbw->lun);
    msc->block_size = tuh_msc_get_block_size(dev_addr, cbw->lun);

    // printf("Disk Size: %" PRIu32 " MB\r\n", block_count / ((1024*1024)/block_size));
    // printf("Block Count = %" PRIu32 ", Block Size: %" PRIu32 "\r\n", block_count, block_size);

    // Check write-protection bit (7) in byte 3
    msc->readonly = (inquiry_resp.protect) ? true : false;
    msc->busy = false;

    msc->connected = true;

    // Add to MSC list
    mp_obj_list_append(usbh_state.msc_list, MP_OBJ_FROM_PTR(msc));

    return true;
}


// Callback when a MSC interface is mounted
static void usbh_msc_mount_cb(uint8_t dev_addr) {
    // Find the device
    machine_usbh_device_obj_t *device = find_device_by_addr(dev_addr);
    if (!device) {
        return;
    }

    uint8_t const lun = 0; // todo support multiple devices

    // Check if device is already registered
    if (find_msc_by_addr_lun(dev_addr, lun) != NULL) {
        return;
    }

    // Create a MSC device object
    machine_usbh_msc_obj_t *msc = mp_obj_malloc(machine_usbh_msc_obj_t, &machine_usbh_msc_type);
    msc->device = device;
    msc->lun = lun;
    msc->connected = true;

    // Get device capacity info
    // uint32_t block_count = tuh_msc_get_block_count(dev_addr, lun);
    // uint32_t block_size = tuh_msc_get_block_size(dev_addr, lun);

    // Check for device details
    tuh_msc_inquiry(dev_addr, lun, &inquiry_resp, usbh_msc_inquiry_complete_cb, (uintptr_t)msc);
}

// Callback when a MSC interface is unmounted
static void usbh_msc_unmount_cb(uint8_t dev_addr) {
    // Find and remove any MSC devices with this address
    for (int i = usbh_state.msc_list->len - 1; i >= 0; i--) {
        machine_usbh_msc_obj_t *msc = MP_OBJ_TO_PTR(usbh_state.msc_list->items[i]);
        if (msc->device->addr == dev_addr) {
            msc->connected = false;
            mp_obj_list_remove(usbh_state.msc_list, MP_OBJ_FROM_PTR(msc));
        }
    }
}

// Callback for CDC RX event
static void usbh_cdc_rx_cb(uint8_t itf_num) {
    // Find the CDC device
    machine_usbh_cdc_obj_t *cdc = find_cdc_by_itf(itf_num);
    if (cdc) {
        cdc->rx_pending = true;

        // Find the matching callback index
        for (int i = 0; i < usbh_state.cdc_list->len; i++) {
            if (usbh_state.cdc_list->items[i] == MP_OBJ_FROM_PTR(cdc)) {
                // If callback exists and not already scheduled, schedule it
                if (usbh_cdc_irq_callback[i] != mp_const_none && !usbh_cdc_irq_scheduled[i]) {
                    mp_sched_schedule(usbh_cdc_irq_callback[i], MP_OBJ_FROM_PTR(cdc));
                    usbh_cdc_irq_scheduled[i] = true;
                }
                break;
            }
        }
    }
}

// Register TinyUSB callbacks
void tuh_mount_cb(uint8_t dev_addr) {
    usbh_device_mount_cb(dev_addr);
}

void tuh_umount_cb(uint8_t dev_addr) {
    usbh_device_unmount_cb(dev_addr);
}

void tuh_cdc_mount_cb(uint8_t itf_num) {
    usbh_cdc_mount_cb(itf_num);
}

void tuh_cdc_umount_cb(uint8_t itf_num) {
    usbh_cdc_unmount_cb(itf_num);
}

void tuh_cdc_rx_cb(uint8_t itf_num) {
    usbh_cdc_rx_cb(itf_num);
}

void tuh_msc_mount_cb(uint8_t dev_addr) {
    usbh_msc_mount_cb(dev_addr);
}

void tuh_msc_umount_cb(uint8_t dev_addr) {
    usbh_msc_unmount_cb(dev_addr);
}

// Callback when a HID device is mounted
static void usbh_hid_mount_cb(uint8_t dev_addr, uint8_t instance, uint8_t const *report_desc, uint16_t desc_len) {
    // Find the device
    machine_usbh_device_obj_t *device = find_device_by_addr(dev_addr);
    if (!device) {
        return;
    }

    // Check if HID device already exists
    if (find_hid_by_addr_instance(dev_addr, instance) != NULL) {
        return;
    }

    // Create a HID device object
    machine_usbh_hid_obj_t *hid = mp_obj_malloc(machine_usbh_hid_obj_t, &machine_usbh_hid_type);
    hid->device = device;
    hid->instance = instance;
    hid->connected = true;
    hid->has_report = false;
    hid->report_len = 0;
    hid->report_id = 0;

    // Determine protocol
    uint8_t const protocol = tuh_hid_interface_protocol(dev_addr, instance);
    switch (protocol) {
        case HID_ITF_PROTOCOL_KEYBOARD:
            hid->protocol = USBH_HID_PROTOCOL_KEYBOARD;
            break;
        case HID_ITF_PROTOCOL_MOUSE:
            hid->protocol = USBH_HID_PROTOCOL_MOUSE;
            break;
        default:
            hid->protocol = USBH_HID_PROTOCOL_GENERIC;
            break;
    }

    // Try to parse usage page/usage from report descriptor (simplified)
    hid->usage_page = 0;
    hid->usage = 0;

    if (desc_len >= 3 && report_desc[0] == 0x05) {
        // Usage Page item
        hid->usage_page = report_desc[1];

        // Look for Usage item
        if (desc_len >= 6 && report_desc[2] == 0x09) {
            hid->usage = report_desc[3];
        }
    }

    // Add to HID list
    mp_obj_list_append(usbh_state.hid_list, MP_OBJ_FROM_PTR(hid));

    // Enable received report callback
    tuh_hid_receive_report(dev_addr, instance);
}

// Callback when a HID device is unmounted
static void usbh_hid_unmount_cb(uint8_t dev_addr, uint8_t instance) {
    // Find the HID device
    machine_usbh_hid_obj_t *hid = find_hid_by_addr_instance(dev_addr, instance);
    if (hid) {
        hid->connected = false;

        // Remove from HID list
        for (int i = 0; i < usbh_state.hid_list->len; i++) {
            if (usbh_state.hid_list->items[i] == MP_OBJ_FROM_PTR(hid)) {
                mp_obj_list_remove(usbh_state.hid_list, MP_OBJ_FROM_PTR(hid));
                break;
            }
        }
    }
}

// Callback when a HID report is received
static void usbh_hid_report_received_cb(uint8_t dev_addr, uint8_t instance, uint8_t const *report, uint16_t len) {
    // Find the HID device
    machine_usbh_hid_obj_t *hid = find_hid_by_addr_instance(dev_addr, instance);
    if (hid) {
        // Store the report
        if (len <= USBH_HID_MAX_REPORT_SIZE) {
            memcpy(hid->report_data, report, len);
            hid->report_len = len;
            hid->has_report = true;

            // Determine report ID if present
            if (tuh_hid_report_id_enabled(dev_addr, instance)) {
                hid->report_id = report[0];
            } else {
                hid->report_id = 0;
            }

            // Find the matching callback index and schedule if needed
            for (int i = 0; i < usbh_state.hid_list->len; i++) {
                if (usbh_state.hid_list->items[i] == MP_OBJ_FROM_PTR(hid)) {
                    // If callback exists and not already scheduled, schedule it
                    if (usbh_hid_irq_callback[i] != mp_const_none && !usbh_hid_irq_scheduled[i]) {
                        mp_sched_schedule(usbh_hid_irq_callback[i], MP_OBJ_FROM_PTR(hid));
                        usbh_hid_irq_scheduled[i] = true;
                    }
                    break;
                }
            }
        }

        // Continue receiving reports
        tuh_hid_receive_report(dev_addr, instance);
    }
}

// TinyUSB HID callbacks
void tuh_hid_mount_cb(uint8_t dev_addr, uint8_t instance, uint8_t const *desc_report, uint16_t desc_len) {
    usbh_hid_mount_cb(dev_addr, instance, desc_report, desc_len);
}

void tuh_hid_umount_cb(uint8_t dev_addr, uint8_t instance) {
    usbh_hid_unmount_cb(dev_addr, instance);
}

void tuh_hid_report_received_cb(uint8_t dev_addr, uint8_t instance, uint8_t const *report, uint16_t len) {
    usbh_hid_report_received_cb(dev_addr, instance, report, len);
}

/******************************************************************************/
// MicroPython bindings for USBHost

// Print function for USBHost
static void machine_usb_host_print(const mp_print_t *print, mp_obj_t self_in, mp_print_kind_t kind) {
    (void)self_in;
    mp_printf(print, "USBHost()");
}

// Create a new USBHost object
static mp_obj_t machine_usb_host_make_new(const mp_obj_type_t *type, size_t n_args, size_t n_kw, const mp_obj_t *args) {
    // Parse arguments (none for now)
    mp_arg_check_num(n_args, n_kw, 0, 0, false);

    // Create the self object
    // machine_usb_host_obj_t *self = mp_obj_malloc(machine_usb_host_obj_t, type);
    return MP_OBJ_FROM_PTR(&usbh_state);
}

// Method to get the list of devices
static mp_obj_t machine_usb_host_devices(mp_obj_t self_in) {
    (void)self_in;
    return MP_OBJ_FROM_PTR(usbh_state.device_list);
}
static MP_DEFINE_CONST_FUN_OBJ_1(machine_usb_host_devices_obj, machine_usb_host_devices);

// Method to get the list of CDC devices
static mp_obj_t machine_usb_host_cdc_devices(mp_obj_t self_in) {
    (void)self_in;
    return MP_OBJ_FROM_PTR(usbh_state.cdc_list);
}
static MP_DEFINE_CONST_FUN_OBJ_1(machine_usb_host_cdc_devices_obj, machine_usb_host_cdc_devices);

// Method to get the list of MSC devices
static mp_obj_t machine_usb_host_msc_devices(mp_obj_t self_in) {
    (void)self_in;
    return MP_OBJ_FROM_PTR(usbh_state.msc_list);
}
static MP_DEFINE_CONST_FUN_OBJ_1(machine_usb_host_msc_devices_obj, machine_usb_host_msc_devices);

// Method to get the list of HID devices
static mp_obj_t machine_usb_host_hid_devices(mp_obj_t self_in) {
    (void)self_in;
    return MP_OBJ_FROM_PTR(usbh_state.hid_list);
}
static MP_DEFINE_CONST_FUN_OBJ_1(machine_usb_host_hid_devices_obj, machine_usb_host_hid_devices);

// Method to check if active
static mp_obj_t machine_usb_host_active(size_t n_args, const mp_obj_t *args) {
    if (n_args == 1) {
        // Get active state
        return mp_obj_new_bool(usbh_state.active);
    } else {
        // Set active state
        if (mp_obj_is_true(args[1])) {
            if (!usbh_state.initialized) {
                // Initialize TinyUSB for host mode
                if (TUD_OPT_RHPORT == BOARD_TUH_RHPORT) { // TUH_OPT_RHPORT
                    tud_deinit(TUD_OPT_RHPORT);
                }
                mp_usbh_init_tuh();
                usbh_state.initialized = true;
            }
            usbh_state.active = true;
        } else {
            usbh_state.active = false;
            tuh_deinit(BOARD_TUH_RHPORT);
            if (TUD_OPT_RHPORT == BOARD_TUH_RHPORT) {
                mp_usbd_init_tud();
            }
        }
        return mp_const_none;
    }
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(machine_usb_host_active_obj, 1, 2, machine_usb_host_active);

// Local dict for USBHost type
static const mp_rom_map_elem_t machine_usb_host_locals_dict_table[] = {
    { MP_ROM_QSTR(MP_QSTR_devices), MP_ROM_PTR(&machine_usb_host_devices_obj) },
    { MP_ROM_QSTR(MP_QSTR_cdc_devices), MP_ROM_PTR(&machine_usb_host_cdc_devices_obj) },
    { MP_ROM_QSTR(MP_QSTR_msc_devices), MP_ROM_PTR(&machine_usb_host_msc_devices_obj) },
    { MP_ROM_QSTR(MP_QSTR_hid_devices), MP_ROM_PTR(&machine_usb_host_hid_devices_obj) },
    { MP_ROM_QSTR(MP_QSTR_active), MP_ROM_PTR(&machine_usb_host_active_obj) },
};
static MP_DEFINE_CONST_DICT(machine_usb_host_locals_dict, machine_usb_host_locals_dict_table);

// Type definition for USBHost
MP_DEFINE_CONST_OBJ_TYPE(
    machine_usb_host_type,
    MP_QSTR_USBHost,
    MP_TYPE_FLAG_NONE,
    make_new, machine_usb_host_make_new,
    print, machine_usb_host_print,
    locals_dict, &machine_usb_host_locals_dict
    );

/******************************************************************************/
// MicroPython bindings for USBHost Device

// Print function for a USB device
static void machine_usbh_device_print(const mp_print_t *print, mp_obj_t self_in, mp_print_kind_t kind) {
    machine_usbh_device_obj_t *self = MP_OBJ_TO_PTR(self_in);
    mp_printf(print, "USBDevice(addr=%u, vid=0x%04x, pid=0x%04x)",
        self->addr, self->vid, self->pid);
}

// Property: vid
static mp_obj_t machine_usbh_device_get_vid(mp_obj_t self_in) {
    machine_usbh_device_obj_t *self = MP_OBJ_TO_PTR(self_in);
    return mp_obj_new_int(self->vid);
}
static MP_DEFINE_CONST_FUN_OBJ_1(machine_usbh_device_get_vid_obj, machine_usbh_device_get_vid);

// Property: pid
static mp_obj_t machine_usbh_device_get_pid(mp_obj_t self_in) {
    machine_usbh_device_obj_t *self = MP_OBJ_TO_PTR(self_in);
    return mp_obj_new_int(self->pid);
}
static MP_DEFINE_CONST_FUN_OBJ_1(machine_usbh_device_get_pid_obj, machine_usbh_device_get_pid);

// Property: serial
static mp_obj_t machine_usbh_device_get_serial(mp_obj_t self_in) {
    machine_usbh_device_obj_t *self = MP_OBJ_TO_PTR(self_in);
    if (self->serial) {
        return mp_obj_new_str(self->serial, strlen(self->serial));
    } else {
        return mp_const_none;
    }
}
static MP_DEFINE_CONST_FUN_OBJ_1(machine_usbh_device_get_serial_obj, machine_usbh_device_get_serial);

// Property: manufacturer
static mp_obj_t machine_usbh_device_get_manufacturer(mp_obj_t self_in) {
    machine_usbh_device_obj_t *self = MP_OBJ_TO_PTR(self_in);
    if (self->manufacturer) {
        return mp_obj_new_str(self->manufacturer, strlen(self->manufacturer));
    } else {
        return mp_const_none;
    }
}
static MP_DEFINE_CONST_FUN_OBJ_1(machine_usbh_device_get_manufacturer_obj, machine_usbh_device_get_manufacturer);

// Property: product
static mp_obj_t machine_usbh_device_get_product(mp_obj_t self_in) {
    machine_usbh_device_obj_t *self = MP_OBJ_TO_PTR(self_in);
    if (self->product) {
        return mp_obj_new_str(self->product, strlen(self->product));
    } else {
        return mp_const_none;
    }
}
static MP_DEFINE_CONST_FUN_OBJ_1(machine_usbh_device_get_product_obj, machine_usbh_device_get_product);

// Local dict for USB device type
static const mp_rom_map_elem_t machine_usbh_device_locals_dict_table[] = {
    { MP_ROM_QSTR(MP_QSTR_vid), MP_ROM_PTR(&machine_usbh_device_get_vid_obj) },
    { MP_ROM_QSTR(MP_QSTR_pid), MP_ROM_PTR(&machine_usbh_device_get_pid_obj) },
    { MP_ROM_QSTR(MP_QSTR_serial), MP_ROM_PTR(&machine_usbh_device_get_serial_obj) },
    { MP_ROM_QSTR(MP_QSTR_manufacturer), MP_ROM_PTR(&machine_usbh_device_get_manufacturer_obj) },
    { MP_ROM_QSTR(MP_QSTR_product), MP_ROM_PTR(&machine_usbh_device_get_product_obj) },
};
static MP_DEFINE_CONST_DICT(machine_usbh_device_locals_dict, machine_usbh_device_locals_dict_table);

// Type definition for USB device
MP_DEFINE_CONST_OBJ_TYPE(
    machine_usbh_device_type,
    MP_QSTR_USBH_Device,
    MP_TYPE_FLAG_NONE,
    print, machine_usbh_device_print,
    locals_dict, &machine_usbh_device_locals_dict
    );

/******************************************************************************/
// MicroPython bindings for USBH_CDC

// Print function for CDC device
static void machine_usbh_cdc_print(const mp_print_t *print, mp_obj_t self_in, mp_print_kind_t kind) {
    machine_usbh_cdc_obj_t *self = MP_OBJ_TO_PTR(self_in);
    mp_printf(print, "USBH_CDC(addr=%u, itf=%u)",
        self->device->addr, self->itf_num);
}

// // Constructor for USBH_CDC
// static mp_obj_t machine_usbh_cdc_make_new(const mp_obj_type_t *type, size_t n_args, size_t n_kw, const mp_obj_t *args) {
//     // CDC devices are created internally, not by the user
//     mp_raise_TypeError(MP_ERROR_TEXT("Cannot create USBH_CDC objects directly"));
//     return mp_const_none;
// }

// Method to check if connected
static mp_obj_t machine_usbh_cdc_is_connected(mp_obj_t self_in) {
    machine_usbh_cdc_obj_t *self = MP_OBJ_TO_PTR(self_in);
    return mp_obj_new_bool(self->connected && self->device->mounted);
}
static MP_DEFINE_CONST_FUN_OBJ_1(machine_usbh_cdc_is_connected_obj, machine_usbh_cdc_is_connected);

// Method to check if data is available
static mp_obj_t machine_usbh_cdc_any(mp_obj_t self_in) {
    machine_usbh_cdc_obj_t *self = MP_OBJ_TO_PTR(self_in);
    if (!self->connected || !self->device->mounted) {
        return MP_OBJ_NEW_SMALL_INT(0);
    }

    uint32_t available = tuh_cdc_read_available(self->itf_num);
    return MP_OBJ_NEW_SMALL_INT(available);
}
static MP_DEFINE_CONST_FUN_OBJ_1(machine_usbh_cdc_any_obj, machine_usbh_cdc_any);

// Method to read data (non-blocking)
static mp_obj_t machine_usbh_cdc_read(size_t n_args, const mp_obj_t *args) {
    machine_usbh_cdc_obj_t *self = MP_OBJ_TO_PTR(args[0]);
    if (!self->connected || !self->device->mounted) {
        mp_raise_OSError(MP_ENODEV);
    }

    mp_int_t size = mp_obj_get_int(args[1]);
    if (size < 0) {
        mp_raise_ValueError(MP_ERROR_TEXT("size must be non-negative"));
    }

    // Create a buffer to read into
    uint8_t *buf = m_new(uint8_t, size);
    if (buf == NULL) {
        mp_raise_OSError(MP_ENOMEM);
    }

    // Read from the CDC device
    uint32_t count = tuh_cdc_read(self->itf_num, buf, size);

    // Create a bytes object with the result
    mp_obj_t bytes = mp_obj_new_bytes(buf, count);
    m_del(uint8_t, buf, size);

    return bytes;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(machine_usbh_cdc_read_obj, 2, 2, machine_usbh_cdc_read);

// Method to write data
static mp_obj_t machine_usbh_cdc_write(mp_obj_t self_in, mp_obj_t data_in) {
    machine_usbh_cdc_obj_t *self = MP_OBJ_TO_PTR(self_in);
    if (!self->connected || !self->device->mounted) {
        mp_raise_OSError(MP_ENODEV);
    }

    // Get the data to write
    mp_buffer_info_t bufinfo;
    mp_get_buffer_raise(data_in, &bufinfo, MP_BUFFER_READ);

    // Write to the CDC device
    uint32_t count = tuh_cdc_write(self->itf_num, bufinfo.buf, bufinfo.len);

    return MP_OBJ_NEW_SMALL_INT(count);
}
static MP_DEFINE_CONST_FUN_OBJ_2(machine_usbh_cdc_write_obj, machine_usbh_cdc_write);

// Method to set up an IRQ handler
static mp_obj_t machine_usbh_cdc_irq(size_t n_args, const mp_obj_t *pos_args, mp_map_t *kw_args) {
    enum { ARG_handler, ARG_trigger, ARG_hard };
    static const mp_arg_t allowed_args[] = {
        { MP_QSTR_handler, MP_ARG_OBJ, {.u_rom_obj = MP_ROM_NONE} },
        { MP_QSTR_trigger, MP_ARG_INT, {.u_int = USBH_CDC_IRQ_RX} },
        { MP_QSTR_hard, MP_ARG_BOOL, {.u_bool = false} },
    };

    machine_usbh_cdc_obj_t *self = MP_OBJ_TO_PTR(pos_args[0]);
    mp_arg_val_t args[MP_ARRAY_SIZE(allowed_args)];
    mp_arg_parse_all(n_args - 1, pos_args + 1, kw_args, MP_ARRAY_SIZE(allowed_args), allowed_args, args);

    if (n_args > 1 || kw_args->used != 0) {
        // Check the handler
        mp_obj_t handler = args[ARG_handler].u_obj;
        if (handler != mp_const_none && !mp_obj_is_callable(handler)) {
            mp_raise_ValueError(MP_ERROR_TEXT("handler must be None or callable"));
        }

        // Check the trigger
        mp_uint_t trigger = args[ARG_trigger].u_int;
        if (trigger == 0) {
            handler = mp_const_none;
        } else if (trigger != USBH_CDC_IRQ_RX) {
            mp_raise_ValueError(MP_ERROR_TEXT("unsupported trigger"));
        }

        // Check hard/soft
        if (args[ARG_hard].u_bool) {
            mp_raise_ValueError(MP_ERROR_TEXT("hard unsupported"));
        }

        // Find the CDC device index
        for (int i = 0; i < usbh_state.cdc_list->len; i++) {
            if (usbh_state.cdc_list->items[i] == MP_OBJ_FROM_PTR(self)) {
                // Set the callback
                usbh_cdc_irq_callback[i] = handler;
                usbh_cdc_irq_scheduled[i] = false;
                break;
            }
        }
    }

    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_KW(machine_usbh_cdc_irq_obj, 1, machine_usbh_cdc_irq);

// Stream read (used by stream protocol)
static mp_uint_t machine_usbh_cdc_stream_read(mp_obj_t self_in, void *buf, mp_uint_t size, int *errcode) {
    machine_usbh_cdc_obj_t *self = MP_OBJ_TO_PTR(self_in);
    if (!self->connected || !self->device->mounted) {
        *errcode = MP_ENODEV;
        return MP_STREAM_ERROR;
    }

    uint32_t count = tuh_cdc_read(self->itf_num, buf, size);
    if (count == 0) {
        // No data available
        *errcode = MP_EAGAIN;
        return MP_STREAM_ERROR;
    }

    return count;
}

// Stream write (used by stream protocol)
static mp_uint_t machine_usbh_cdc_stream_write(mp_obj_t self_in, const void *buf, mp_uint_t size, int *errcode) {
    machine_usbh_cdc_obj_t *self = MP_OBJ_TO_PTR(self_in);
    if (!self->connected || !self->device->mounted) {
        *errcode = MP_ENODEV;
        return MP_STREAM_ERROR;
    }

    uint32_t count = tuh_cdc_write(self->itf_num, buf, size);
    if (count == 0) {
        // Could not write any data
        *errcode = MP_EAGAIN;
        return MP_STREAM_ERROR;
    }

    return count;
}

// Stream ioctl (used by stream protocol)
static mp_uint_t machine_usbh_cdc_stream_ioctl(mp_obj_t self_in, mp_uint_t request, uintptr_t arg, int *errcode) {
    machine_usbh_cdc_obj_t *self = MP_OBJ_TO_PTR(self_in);
    mp_uint_t ret;

    if (request == MP_STREAM_POLL) {
        mp_uint_t flags = arg;
        ret = 0;

        if ((flags & MP_STREAM_POLL_RD) && tuh_cdc_read_available(self->itf_num) > 0) {
            ret |= MP_STREAM_POLL_RD;
        }

        if ((flags & MP_STREAM_POLL_WR) && self->connected && self->device->mounted) {
            ret |= MP_STREAM_POLL_WR;
        }
    } else {
        *errcode = MP_EINVAL;
        ret = MP_STREAM_ERROR;
    }

    return ret;
}

// Stream protocol
static const mp_stream_p_t machine_usbh_cdc_stream_p = {
    .read = machine_usbh_cdc_stream_read,
    .write = machine_usbh_cdc_stream_write,
    .ioctl = machine_usbh_cdc_stream_ioctl,
};

// Local dict for USBH_CDC type
static const mp_rom_map_elem_t machine_usbh_cdc_locals_dict_table[] = {
    { MP_ROM_QSTR(MP_QSTR_is_connected), MP_ROM_PTR(&machine_usbh_cdc_is_connected_obj) },
    { MP_ROM_QSTR(MP_QSTR_any), MP_ROM_PTR(&machine_usbh_cdc_any_obj) },
    { MP_ROM_QSTR(MP_QSTR_read), MP_ROM_PTR(&machine_usbh_cdc_read_obj) },
    { MP_ROM_QSTR(MP_QSTR_write), MP_ROM_PTR(&machine_usbh_cdc_write_obj) },
    { MP_ROM_QSTR(MP_QSTR_irq), MP_ROM_PTR(&machine_usbh_cdc_irq_obj) },

    // Stream methods
    { MP_ROM_QSTR(MP_QSTR_readline), MP_ROM_PTR(&mp_stream_unbuffered_readline_obj) },
    { MP_ROM_QSTR(MP_QSTR_readinto), MP_ROM_PTR(&mp_stream_readinto_obj) },
    { MP_ROM_QSTR(MP_QSTR_readlines), MP_ROM_PTR(&mp_stream_unbuffered_readlines_obj) },

    // Class constants
    { MP_ROM_QSTR(MP_QSTR_IRQ_RX), MP_ROM_INT(USBH_CDC_IRQ_RX) },
};
static MP_DEFINE_CONST_DICT(machine_usbh_cdc_locals_dict, machine_usbh_cdc_locals_dict_table);

// Type definition for USBH_CDC
MP_DEFINE_CONST_OBJ_TYPE(
    machine_usbh_cdc_type,
    MP_QSTR_USBH_CDC,
    MP_TYPE_FLAG_ITER_IS_STREAM,
    // make_new, machine_usbh_cdc_make_new,
    print, machine_usbh_cdc_print,
    protocol, &machine_usbh_cdc_stream_p,
    locals_dict, &machine_usbh_cdc_locals_dict
    );

/******************************************************************************/
// MicroPython bindings for USBH_MSC (Block Device)

// Print function for MSC device
static void machine_usbh_msc_print(const mp_print_t *print, mp_obj_t self_in, mp_print_kind_t kind) {
    machine_usbh_msc_obj_t *self = MP_OBJ_TO_PTR(self_in);
    mp_printf(print, "USBH_MSC(addr=%u, lun=%u, block_size=%u, block_count=%u, readonly=%d)",
        self->device->addr, self->lun, self->block_size, self->block_count, self->readonly);
}

// // Constructor for USBH_MSC
// static mp_obj_t machine_usbh_msc_make_new(const mp_obj_type_t *type, size_t n_args, size_t n_kw, const mp_obj_t *args) {
//     // MSC devices are created internally, not by the user
//     mp_raise_TypeError(MP_ERROR_TEXT("Cannot create USBH_MSC objects directly"));
//     return mp_const_none;
// }

// Method to check if connected
static mp_obj_t machine_usbh_msc_is_connected(mp_obj_t self_in) {
    machine_usbh_msc_obj_t *self = MP_OBJ_TO_PTR(self_in);
    return mp_obj_new_bool(self->connected && self->device->mounted);
}
static MP_DEFINE_CONST_FUN_OBJ_1(machine_usbh_msc_is_connected_obj, machine_usbh_msc_is_connected);

static bool machine_usbh_msc_io_complete(uint8_t dev_addr, tuh_msc_complete_data_t const *cb_data) {
    machine_usbh_msc_obj_t *msc = (machine_usbh_msc_obj_t *)(cb_data->user_arg);
    if (msc) {
        msc->busy = false;
        return true;
    }
    return false;
}

// Method to readblocks for block protocol
static mp_obj_t machine_usbh_msc_readblocks(size_t n_args, const mp_obj_t *args) {
    machine_usbh_msc_obj_t *self = MP_OBJ_TO_PTR(args[0]);
    if (!self->connected || !self->device->mounted) {
        mp_raise_OSError(MP_ENODEV);
    }

    uint32_t block_num = mp_obj_get_int(args[1]);
    mp_buffer_info_t bufinfo;
    mp_get_buffer_raise(args[2], &bufinfo, MP_BUFFER_WRITE);

    uint32_t offset = 0;
    if (n_args == 4) {
        offset = mp_obj_get_int(args[3]);
        if (offset != 0) {
            // todo I think we'd need to do a read/modify/write here
            mp_raise_ValueError(MP_ERROR_TEXT("partial block writes not supported"));
        }
    }

    uint32_t count = bufinfo.len / self->block_size;
    self->busy = true;
    tuh_msc_read10(self->device->addr, self->lun,
        bufinfo.buf, block_num, count, machine_usbh_msc_io_complete, (uintptr_t)self);

    // Wait for the operation to complete
    while (!tuh_msc_ready(self->device->addr)) {
        tuh_task();  // Process USB events until ready
    }
    if (self->busy) {
        // Shouldn't get here unless tuh_msc_ready() doesn't work
        mp_raise_OSError(MP_EINPROGRESS);
    }

    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(machine_usbh_msc_readblocks_obj, 3, 4, machine_usbh_msc_readblocks);

// Method to writeblocks for block protocol
static mp_obj_t machine_usbh_msc_writeblocks(size_t n_args, const mp_obj_t *args) {
    machine_usbh_msc_obj_t *self = MP_OBJ_TO_PTR(args[0]);
    if (!self->connected || !self->device->mounted) {
        mp_raise_OSError(MP_ENODEV);
    }

    if (self->readonly) {
        mp_raise_OSError(MP_EROFS);  // Read-only file system
    }

    if (self->busy) {
        mp_raise_OSError(MP_EBUSY);  // Read-only file system
    }

    uint32_t block_num = mp_obj_get_int(args[1]);
    mp_buffer_info_t bufinfo;
    mp_get_buffer_raise(args[2], &bufinfo, MP_BUFFER_READ);

    uint32_t offset = 0;
    if (n_args == 4) {
        offset = mp_obj_get_int(args[3]);
        if (offset != 0) {
            // todo I think we'd need to do a read/modify/write here
            mp_raise_ValueError(MP_ERROR_TEXT("partial block writes not supported"));
        }
    }

    uint32_t count = bufinfo.len / self->block_size;
    self->busy = true;
    tuh_msc_write10(self->device->addr, self->lun,
        bufinfo.buf, block_num, count, machine_usbh_msc_io_complete, (uintptr_t)self);

    // Wait for the operation to complete
    while (!tuh_msc_ready(self->device->addr)) {
        tuh_task();  // Process USB events until ready
    }
    if (self->busy) {
        // Shouldn't get here unless tuh_msc_ready() doesn't work
        mp_raise_OSError(MP_EINPROGRESS);
    }

    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(machine_usbh_msc_writeblocks_obj, 3, 4, machine_usbh_msc_writeblocks);

// Method to ioctl for block protocol
static mp_obj_t machine_usbh_msc_ioctl(mp_obj_t self_in, mp_obj_t cmd_in, mp_obj_t arg_in) {
    machine_usbh_msc_obj_t *self = MP_OBJ_TO_PTR(self_in);
    mp_int_t cmd = mp_obj_get_int(cmd_in);

    switch (cmd) {
        case MP_BLOCKDEV_IOCTL_INIT:
            return MP_OBJ_NEW_SMALL_INT(0);  // Always initialized

        case MP_BLOCKDEV_IOCTL_DEINIT:
            return MP_OBJ_NEW_SMALL_INT(0);  // Cannot be deinitialized

        case MP_BLOCKDEV_IOCTL_SYNC:
            // Nothing to do for sync
            return MP_OBJ_NEW_SMALL_INT(0);

        case MP_BLOCKDEV_IOCTL_BLOCK_COUNT:
            return MP_OBJ_NEW_SMALL_INT(self->block_count);

        case MP_BLOCKDEV_IOCTL_BLOCK_SIZE:
            return MP_OBJ_NEW_SMALL_INT(self->block_size);

        case MP_BLOCKDEV_IOCTL_BLOCK_ERASE:
            // Not supported for USB MSC devices
            return MP_OBJ_NEW_SMALL_INT(0);

        default:
            return mp_const_none;
    }
}
static MP_DEFINE_CONST_FUN_OBJ_3(machine_usbh_msc_ioctl_obj, machine_usbh_msc_ioctl);

// Local dict for USBH_MSC type
static const mp_rom_map_elem_t machine_usbh_msc_locals_dict_table[] = {
    { MP_ROM_QSTR(MP_QSTR_is_connected), MP_ROM_PTR(&machine_usbh_msc_is_connected_obj) },
    { MP_ROM_QSTR(MP_QSTR_readblocks), MP_ROM_PTR(&machine_usbh_msc_readblocks_obj) },
    { MP_ROM_QSTR(MP_QSTR_writeblocks), MP_ROM_PTR(&machine_usbh_msc_writeblocks_obj) },
    { MP_ROM_QSTR(MP_QSTR_ioctl), MP_ROM_PTR(&machine_usbh_msc_ioctl_obj) },
};
static MP_DEFINE_CONST_DICT(machine_usbh_msc_locals_dict, machine_usbh_msc_locals_dict_table);

// Type definition for USBH_MSC
MP_DEFINE_CONST_OBJ_TYPE(
    machine_usbh_msc_type,
    MP_QSTR_USBH_MSC,
    MP_TYPE_FLAG_NONE,
    // make_new, machine_usbh_msc_make_new,
    print, machine_usbh_msc_print,
    locals_dict, &machine_usbh_msc_locals_dict
    );

/******************************************************************************/
// MicroPython bindings for USBH_HID

// Print function for HID device
static void machine_usbh_hid_print(const mp_print_t *print, mp_obj_t self_in, mp_print_kind_t kind) {
    machine_usbh_hid_obj_t *self = MP_OBJ_TO_PTR(self_in);
    mp_printf(print, "USBH_HID(addr=%u, instance=%u, protocol=%u, usage_page=0x%04x, usage=0x%04x)",
        self->device->addr, self->instance, self->protocol, self->usage_page, self->usage);
}

// Method to check if connected
static mp_obj_t machine_usbh_hid_is_connected(mp_obj_t self_in) {
    machine_usbh_hid_obj_t *self = MP_OBJ_TO_PTR(self_in);
    return mp_obj_new_bool(self->connected && self->device->mounted);
}
static MP_DEFINE_CONST_FUN_OBJ_1(machine_usbh_hid_is_connected_obj, machine_usbh_hid_is_connected);

// Method to check protocol
static mp_obj_t machine_usbh_hid_get_protocol(mp_obj_t self_in) {
    machine_usbh_hid_obj_t *self = MP_OBJ_TO_PTR(self_in);
    return MP_OBJ_NEW_SMALL_INT(self->protocol);
}
static MP_DEFINE_CONST_FUN_OBJ_1(machine_usbh_hid_get_protocol_obj, machine_usbh_hid_get_protocol);

// Method to check if report is available
static mp_obj_t machine_usbh_hid_has_report(mp_obj_t self_in) {
    machine_usbh_hid_obj_t *self = MP_OBJ_TO_PTR(self_in);
    return mp_obj_new_bool(self->has_report);
}
static MP_DEFINE_CONST_FUN_OBJ_1(machine_usbh_hid_has_report_obj, machine_usbh_hid_has_report);

// Method to get report data
static mp_obj_t machine_usbh_hid_get_report(mp_obj_t self_in) {
    machine_usbh_hid_obj_t *self = MP_OBJ_TO_PTR(self_in);
    if (!self->has_report) {
        return mp_const_none;
    }

    // Create a bytes object with the report data
    mp_obj_t report = mp_obj_new_bytes(self->report_data, self->report_len);

    // Clear the has_report flag
    self->has_report = false;

    return report;
}
static MP_DEFINE_CONST_FUN_OBJ_1(machine_usbh_hid_get_report_obj, machine_usbh_hid_get_report);

// Method to get report ID
static mp_obj_t machine_usbh_hid_get_report_id(mp_obj_t self_in) {
    machine_usbh_hid_obj_t *self = MP_OBJ_TO_PTR(self_in);
    return MP_OBJ_NEW_SMALL_INT(self->report_id);
}
static MP_DEFINE_CONST_FUN_OBJ_1(machine_usbh_hid_get_report_id_obj, machine_usbh_hid_get_report_id);

// Method to get usage page
static mp_obj_t machine_usbh_hid_get_usage_page(mp_obj_t self_in) {
    machine_usbh_hid_obj_t *self = MP_OBJ_TO_PTR(self_in);
    return MP_OBJ_NEW_SMALL_INT(self->usage_page);
}
static MP_DEFINE_CONST_FUN_OBJ_1(machine_usbh_hid_get_usage_page_obj, machine_usbh_hid_get_usage_page);

// Method to get usage
static mp_obj_t machine_usbh_hid_get_usage(mp_obj_t self_in) {
    machine_usbh_hid_obj_t *self = MP_OBJ_TO_PTR(self_in);
    return MP_OBJ_NEW_SMALL_INT(self->usage);
}
static MP_DEFINE_CONST_FUN_OBJ_1(machine_usbh_hid_get_usage_obj, machine_usbh_hid_get_usage);

// Method for setting up IRQ handler
static mp_obj_t machine_usbh_hid_irq(size_t n_args, const mp_obj_t *pos_args, mp_map_t *kw_args) {
    enum { ARG_handler, ARG_trigger, ARG_hard };
    static const mp_arg_t allowed_args[] = {
        { MP_QSTR_handler, MP_ARG_OBJ, {.u_rom_obj = MP_ROM_NONE} },
        { MP_QSTR_trigger, MP_ARG_INT, {.u_int = USBH_HID_IRQ_REPORT} },
        { MP_QSTR_hard, MP_ARG_BOOL, {.u_bool = false} },
    };

    machine_usbh_hid_obj_t *self = MP_OBJ_TO_PTR(pos_args[0]);
    mp_arg_val_t args[MP_ARRAY_SIZE(allowed_args)];
    mp_arg_parse_all(n_args - 1, pos_args + 1, kw_args, MP_ARRAY_SIZE(allowed_args), allowed_args, args);

    if (n_args > 1 || kw_args->used != 0) {
        // Check the handler
        mp_obj_t handler = args[ARG_handler].u_obj;
        if (handler != mp_const_none && !mp_obj_is_callable(handler)) {
            mp_raise_ValueError(MP_ERROR_TEXT("handler must be None or callable"));
        }

        // Check the trigger
        mp_uint_t trigger = args[ARG_trigger].u_int;
        if (trigger == 0) {
            handler = mp_const_none;
        } else if (trigger != USBH_HID_IRQ_REPORT) {
            mp_raise_ValueError(MP_ERROR_TEXT("unsupported trigger"));
        }

        // Check hard/soft
        if (args[ARG_hard].u_bool) {
            mp_raise_ValueError(MP_ERROR_TEXT("hard unsupported"));
        }

        // Find the HID device index
        for (int i = 0; i < usbh_state.hid_list->len; i++) {
            if (usbh_state.hid_list->items[i] == MP_OBJ_FROM_PTR(self)) {
                // Set the callback
                usbh_hid_irq_callback[i] = handler;
                usbh_hid_irq_scheduled[i] = false;
                break;
            }
        }
    }

    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_KW(machine_usbh_hid_irq_obj, 1, machine_usbh_hid_irq);

// Method to request a report from the device
static mp_obj_t machine_usbh_hid_request_report(mp_obj_t self_in) {
    machine_usbh_hid_obj_t *self = MP_OBJ_TO_PTR(self_in);
    if (!self->connected || !self->device->mounted) {
        mp_raise_OSError(MP_ENODEV);
    }

    // Request a report from the device
    bool success = tuh_hid_receive_report(self->device->addr, self->instance);
    return mp_obj_new_bool(success);
}
static MP_DEFINE_CONST_FUN_OBJ_1(machine_usbh_hid_request_report_obj, machine_usbh_hid_request_report);

// Method to send a report to the device (for output reports)
static mp_obj_t machine_usbh_hid_send_report(mp_obj_t self_in, mp_obj_t report_in) {
    machine_usbh_hid_obj_t *self = MP_OBJ_TO_PTR(self_in);
    if (!self->connected || !self->device->mounted) {
        mp_raise_OSError(MP_ENODEV);
    }

    // Get the report data
    mp_buffer_info_t bufinfo;
    mp_get_buffer_raise(report_in, &bufinfo, MP_BUFFER_READ);

    if (bufinfo.len > USBH_HID_MAX_REPORT_SIZE) {
        mp_raise_ValueError(MP_ERROR_TEXT("report too large"));
    }

    // Send the report
    bool success = tuh_hid_send_report(self->device->addr, self->instance, 0, bufinfo.buf, bufinfo.len);
    return mp_obj_new_bool(success);
}
static MP_DEFINE_CONST_FUN_OBJ_2(machine_usbh_hid_send_report_obj, machine_usbh_hid_send_report);

// Local dict for USBH_HID type
static const mp_rom_map_elem_t machine_usbh_hid_locals_dict_table[] = {
    { MP_ROM_QSTR(MP_QSTR_is_connected), MP_ROM_PTR(&machine_usbh_hid_is_connected_obj) },
    { MP_ROM_QSTR(MP_QSTR_protocol), MP_ROM_PTR(&machine_usbh_hid_get_protocol_obj) },
    { MP_ROM_QSTR(MP_QSTR_has_report), MP_ROM_PTR(&machine_usbh_hid_has_report_obj) },
    { MP_ROM_QSTR(MP_QSTR_get_report), MP_ROM_PTR(&machine_usbh_hid_get_report_obj) },
    { MP_ROM_QSTR(MP_QSTR_report_id), MP_ROM_PTR(&machine_usbh_hid_get_report_id_obj) },
    { MP_ROM_QSTR(MP_QSTR_usage_page), MP_ROM_PTR(&machine_usbh_hid_get_usage_page_obj) },
    { MP_ROM_QSTR(MP_QSTR_usage), MP_ROM_PTR(&machine_usbh_hid_get_usage_obj) },
    { MP_ROM_QSTR(MP_QSTR_irq), MP_ROM_PTR(&machine_usbh_hid_irq_obj) },
    { MP_ROM_QSTR(MP_QSTR_request_report), MP_ROM_PTR(&machine_usbh_hid_request_report_obj) },
    { MP_ROM_QSTR(MP_QSTR_send_report), MP_ROM_PTR(&machine_usbh_hid_send_report_obj) },

    // Protocol constants
    { MP_ROM_QSTR(MP_QSTR_PROTOCOL_NONE), MP_ROM_INT(USBH_HID_PROTOCOL_NONE) },
    { MP_ROM_QSTR(MP_QSTR_PROTOCOL_KEYBOARD), MP_ROM_INT(USBH_HID_PROTOCOL_KEYBOARD) },
    { MP_ROM_QSTR(MP_QSTR_PROTOCOL_MOUSE), MP_ROM_INT(USBH_HID_PROTOCOL_MOUSE) },
    { MP_ROM_QSTR(MP_QSTR_PROTOCOL_GENERIC), MP_ROM_INT(USBH_HID_PROTOCOL_GENERIC) },

    // IRQ constants
    { MP_ROM_QSTR(MP_QSTR_IRQ_REPORT), MP_ROM_INT(USBH_HID_IRQ_REPORT) },
};
static MP_DEFINE_CONST_DICT(machine_usbh_hid_locals_dict, machine_usbh_hid_locals_dict_table);

// Type definition for USBH_HID
MP_DEFINE_CONST_OBJ_TYPE(
    machine_usbh_hid_type,
    MP_QSTR_USBH_HID,
    MP_TYPE_FLAG_NONE,
    print, machine_usbh_hid_print,
    locals_dict, &machine_usbh_hid_locals_dict
    );

#endif // MICROPY_HW_USB_HOST
