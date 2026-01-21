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
#include <stddef.h>

#ifndef NO_QSTR
#include "tusb.h"
#endif

#if MICROPY_HW_USB_HOST

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

// MSC operation timeout (milliseconds)
#ifndef MICROPY_HW_USBH_MSC_TIMEOUT
#define MICROPY_HW_USBH_MSC_TIMEOUT 5000
#endif

// Forward declare STM32 low-level USB init (bypasses tusb_inited() check)
#if defined(STM32F4) || defined(STM32F7) || defined(STM32F2)
extern void mp_usbd_ll_init_fs(void);
extern void mp_usbh_ll_init_vbus_fs(void);
#endif

// Port-specific USB host PHY initialization.
#if defined(CONFIG_IDF_TARGET_ESP32S2) || defined(CONFIG_IDF_TARGET_ESP32S3) || defined(CONFIG_IDF_TARGET_ESP32P4)
extern void usb_phy_init_host(void);
#endif

// Enable/disable USB host interrupts.
void mp_usbh_int_enable(void);
void mp_usbh_int_disable(void);

// Initialize TinyUSB for host mode.
static inline void mp_usbh_init_tuh(void) {
    // On single-port boards where host and device share the same USB controller,
    // properly deinitialize device mode before switching to host mode.
    // This removes the device IRQ handler before hcd_init() resets the hardware,
    // preventing the device ISR from firing during the transition.
    #if MICROPY_HW_ENABLE_USBDEV && (BOARD_TUH_RHPORT == TUD_OPT_RHPORT)
    tud_deinit(TUD_OPT_RHPORT);
    #endif
    // Reset USB peripheral to clear any device mode configuration
    #if defined(STM32F4) && defined(__HAL_RCC_USB_OTG_FS_FORCE_RESET)
    __HAL_RCC_USB_OTG_FS_FORCE_RESET();
    // Add delay after reset for peripheral to stabilize
    for (volatile int i = 0; i < 10000; i++) {;
    }
    __HAL_RCC_USB_OTG_FS_RELEASE_RESET();
    for (volatile int i = 0; i < 10000; i++) {;
    }
    #endif
    // Initialize USB hardware - call low-level init directly to bypass tusb_inited() check
    // which prevents re-init when switching from device to host mode
    #if defined(STM32F4) || defined(STM32F7) || defined(STM32F2)
    mp_usbd_ll_init_fs();
    mp_usbh_ll_init_vbus_fs();
    #elif defined(CONFIG_IDF_TARGET_ESP32S2) || defined(CONFIG_IDF_TARGET_ESP32S3) || defined(CONFIG_IDF_TARGET_ESP32P4)
    usb_phy_init_host();
    #elif defined(MICROPY_HW_TINYUSB_LL_INIT)
    MICROPY_HW_TINYUSB_LL_INIT();
    #endif
    // Pre-set host role before tuh_init() to prevent an ISR race condition:
    // tuh_init() enables the host interrupt before setting _tusb_rhport_role,
    // so if a device is already connected the ISR fires immediately, finds
    // role=INVALID, leaves the interrupt uncleared, and loops forever.
    #ifndef NO_QSTR
    {
        // TinyUSB internal (verified against TinyUSB 0.20.0 / commit 3af1bec1a9).
        // TODO: propose upstream fix so tuh_init() sets role before enabling interrupts.
        extern tusb_role_t _tusb_rhport_role[];
        _tusb_rhport_role[BOARD_TUH_RHPORT] = TUSB_ROLE_HOST;
    }
    #endif
    tuh_init(BOARD_TUH_RHPORT);
    // Note: tuh_init() already calls hcd_int_enable() internally.
    // Don't call mp_usbh_int_enable() again as it causes double interrupt
    // allocation on ESP32 (esp_intr_alloc called twice).
}

// Fetch string descriptors (manufacturer/product/serial) for a device.
// Called lazily on first property access. Safe to call from Python context only.
void mp_usbh_fetch_device_strings(machine_usbh_device_obj_t *dev);

// Schedule a call to mp_usbd_task(), even if no USB interrupt has occurred
void mp_usbh_schedule_task(void);
void mp_usbh_task(void);

// Helper function to wait for MSC operation completion.
bool mp_usbh_msc_wait_complete(machine_usbh_msc_obj_t *msc, uint32_t timeout_ms);

// MSC transfer complete callback for tuh_msc_read10/write10
#ifndef NO_QSTR
bool mp_usbh_msc_xfer_complete(uint8_t dev_addr, tuh_msc_complete_data_t const *cb_data);
#endif

// Deinitialization function
void mp_usbh_deinit(void);

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

// Structure to track USB device information
typedef struct _machine_usbh_device_obj_t {
    mp_obj_base_t base;
    uint8_t addr;               // Device address
    uint16_t vid;               // Vendor ID
    uint16_t pid;               // Product ID
    char manufacturer[64];      // Manufacturer string (UTF-8, empty if unavailable)
    char product[64];           // Product string (UTF-8, empty if unavailable)
    char serial[64];            // Serial number string (UTF-8, empty if unavailable)
    uint8_t dev_class;          // Device class
    uint8_t dev_subclass;       // Device subclass
    uint8_t dev_protocol;       // Device protocol
    volatile bool mounted;      // Whether the device is currently mounted
    bool strings_fetched;       // Whether string descriptors have been fetched
} machine_usbh_device_obj_t;

// Structure for USB CDC device
typedef struct _machine_usbh_cdc_obj_t {
    mp_obj_base_t base;
    uint8_t dev_addr;           // Parent device
    volatile bool connected;    // Whether device is connected
    uint8_t itf_num;            // Interface number
    mp_obj_t irq_callback;      // CDC IRQ callbacks
} machine_usbh_cdc_obj_t;

// Structure for USB MSC device (block device)
typedef struct _machine_usbh_msc_obj_t {
    mp_obj_base_t base;
    uint8_t dev_addr;           // Parent device
    volatile bool connected;    // Whether device is connected
    uint8_t lun;                // Logical Unit Number
    uint32_t block_size;        // Block size in bytes
    uint32_t block_count;       // Number of blocks
    bool readonly;              // Whether the device is read-only
    // Async operation completion tracking
    volatile bool operation_pending;
    volatile bool operation_success;
} machine_usbh_msc_obj_t;

// Structure for USB HID device
typedef struct _machine_usbh_hid_obj_t {
    mp_obj_base_t base;
    uint8_t dev_addr;           // Parent device
    volatile bool connected;    // Whether device is connected
    uint8_t instance;           // HID instance (different from interface number)
    uint8_t protocol;           // HID protocol (KEYBOARD, MOUSE, GENERIC)
    uint16_t usage_page;        // HID usage page
    uint16_t usage;             // HID usage
    // Pre-allocated report buffer (no allocation in callback)
    uint8_t report_buffer[USBH_HID_MAX_REPORT_SIZE];
    volatile uint16_t report_len;
    volatile bool report_ready;
    mp_obj_t irq_callback;      // HID IRQ callbacks
} machine_usbh_hid_obj_t;

// Structure to hold shared USB host state.
// Uses pre-allocated static pools indexed by TinyUSB device address/index
// to avoid heap allocation inside TinyUSB callbacks.
typedef struct _mp_obj_usb_host_t {
    mp_obj_base_t base;

    bool initialized;                  // Whether TinyUSB host support is initialized
    bool active;                       // Whether USB host is active

    // Pre-allocated device pools sized by TinyUSB limits.
    // Indexed by dev_addr-1 (device) or TinyUSB class index (CDC/MSC/HID).
    machine_usbh_device_obj_t device_pool[CFG_TUH_DEVICE_MAX];
    machine_usbh_cdc_obj_t cdc_pool[CFG_TUH_CDC];
    machine_usbh_msc_obj_t msc_pool[CFG_TUH_MSC];
    machine_usbh_hid_obj_t hid_pool[CFG_TUH_HID];
} mp_obj_usb_host_t;

// Helper function to check if a device is mounted.
bool mp_usbh_device_mounted(uint8_t dev_addr);

// This macro can accept any of the device types (cdc, msc, hid) above.
#define DEVICE_ACTIVE(self) (self->connected && mp_usbh_device_mounted(self->dev_addr))

// Register root pointer for USB host object
MP_REGISTER_ROOT_POINTER(struct _mp_obj_usb_host_t *usbh);

#endif // MICROPY_HW_USB_HOST

#endif // MICROPY_INCLUDED_SHARED_TINYUSB_MP_USBH_H
