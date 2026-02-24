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

#include "mp_usbh.h"

#if MICROPY_HW_USB_HOST

#ifndef NO_QSTR
#include "tusb.h"
#include "host/hcd.h"
#include "host/usbh.h"
#include "class/cdc/cdc_host.h"
#include "class/msc/msc_host.h"
#include "class/hid/hid_host.h"
#if CFG_TUH_RPI_PIO_USB
#include "pio_usb.h"
#endif
#endif

// Helper functions to find devices by pool index.
static machine_usbh_device_obj_t *find_device_by_addr(uint8_t addr) {
    mp_obj_usb_host_t *usbh = MP_OBJ_TO_PTR(MP_STATE_VM(usbh));
    if (usbh == NULL || addr == 0 || addr > CFG_TUH_DEVICE_MAX) {
        return NULL;
    }
    machine_usbh_device_obj_t *dev = &usbh->device_pool[addr - 1];
    return dev->mounted ? dev : NULL;
}

static machine_usbh_cdc_obj_t *find_cdc_by_itf(uint8_t itf_num) {
    mp_obj_usb_host_t *usbh = MP_OBJ_TO_PTR(MP_STATE_VM(usbh));
    if (usbh == NULL || itf_num >= CFG_TUH_CDC) {
        return NULL;
    }
    machine_usbh_cdc_obj_t *cdc = &usbh->cdc_pool[itf_num];
    return cdc->connected ? cdc : NULL;
}

static machine_usbh_msc_obj_t *find_msc_by_addr(uint8_t addr) {
    mp_obj_usb_host_t *usbh = MP_OBJ_TO_PTR(MP_STATE_VM(usbh));
    if (usbh == NULL) {
        return NULL;
    }
    for (int i = 0; i < CFG_TUH_MSC; i++) {
        machine_usbh_msc_obj_t *msc = &usbh->msc_pool[i];
        if (msc->connected && msc->dev_addr == addr) {
            return msc;
        }
    }
    return NULL;
}

static machine_usbh_hid_obj_t *find_hid_by_addr_instance(uint8_t addr, uint8_t instance) {
    mp_obj_usb_host_t *usbh = MP_OBJ_TO_PTR(MP_STATE_VM(usbh));
    if (usbh == NULL) {
        return NULL;
    }
    for (int i = 0; i < CFG_TUH_HID; i++) {
        machine_usbh_hid_obj_t *hid = &usbh->hid_pool[i];
        if (hid->connected && hid->dev_addr == addr && hid->instance == instance) {
            return hid;
        }
    }
    return NULL;
}

// Helper function to check if a device is connected / mounted.
bool mp_usbh_device_mounted(uint8_t dev_addr) {
    machine_usbh_device_obj_t *device = find_device_by_addr(dev_addr);
    return device != NULL;
}

// Initialize TinyUSB for host mode.
void mp_usbh_init_tuh(void) {
    // On single-port boards where host and device share the same USB controller,
    // properly deinitialize device mode before switching to host mode.
    // This removes the device IRQ handler before hcd_init() resets the hardware,
    // preventing the device ISR from firing during the transition.
    #if MICROPY_HW_ENABLE_USBDEV && (BOARD_TUH_RHPORT == TUD_OPT_RHPORT)
    tud_deinit(TUD_OPT_RHPORT);
    #endif

    // Reset USB peripheral to clear any device mode configuration.
    #if defined(STM32F4) && defined(__HAL_RCC_USB_OTG_FS_FORCE_RESET)
    __HAL_RCC_USB_OTG_FS_FORCE_RESET();
    for (volatile int i = 0; i < 10000; i++) {
        ;
    }
    __HAL_RCC_USB_OTG_FS_RELEASE_RESET();
    for (volatile int i = 0; i < 10000; i++) {
        ;
    }
    #endif

    // Initialize USB hardware — call low-level init directly to bypass
    // tusb_inited() check which prevents re-init when switching from
    // device to host mode.
    #if defined(STM32F4) || defined(STM32F7) || defined(STM32F2)
    mp_usbd_ll_init_fs();
    mp_usbh_ll_init_vbus_fs();
    #elif defined(CONFIG_IDF_TARGET_ESP32S2) || defined(CONFIG_IDF_TARGET_ESP32S3) || defined(CONFIG_IDF_TARGET_ESP32P4)
    usb_phy_init_host();
    #elif defined(MICROPY_HW_TINYUSB_LL_INIT)
    MICROPY_HW_TINYUSB_LL_INIT();
    #endif

    // Configure PIO USB pin mapping before tuh_init().
    #if CFG_TUH_RPI_PIO_USB && defined(MICROPY_HW_USB_HOST_DP_PIN)
    #ifndef NO_QSTR
    {
        pio_usb_configuration_t pio_cfg = PIO_USB_DEFAULT_CONFIG;
        pio_cfg.pin_dp = MICROPY_HW_USB_HOST_DP_PIN;
        // Use the default pico-sdk alarm pool instead of creating a new one.
        // Hardware alarm 2 (the default for pio-usb alarm_pool_create) is already
        // claimed by MicroPython's soft timer (MICROPY_HW_SOFT_TIMER_ALARM_NUM).
        pio_cfg.alarm_pool = alarm_pool_get_default();
        tuh_configure(BOARD_TUH_RHPORT, TUH_CFGID_RPI_PIO_USB_CONFIGURATION, &pio_cfg);
    }
    #endif
    #endif

    // Pre-set host role before tuh_init() to prevent an ISR race condition:
    // tuh_init() enables the host interrupt before setting _tusb_rhport_role,
    // so if a device is already connected the ISR fires immediately, finds
    // role=INVALID, leaves the interrupt uncleared, and loops forever.
    #ifndef NO_QSTR
    {
        // TinyUSB internal: pre-set host role before tuh_init() to prevent ISR
        // race condition where interrupt fires before role is set.
        // TODO: propose upstream fix so tuh_init() sets role before enabling interrupts.
        #if TUSB_VERSION_MAJOR == 0 && TUSB_VERSION_MINOR <= 20
        extern tusb_role_t _tusb_rhport_role[];
        _tusb_rhport_role[BOARD_TUH_RHPORT] = TUSB_ROLE_HOST;
        #else
        #warning "Check if TinyUSB still needs pre-setting _tusb_rhport_role before tuh_init()"
        #endif
    }
    #endif

    tuh_init(BOARD_TUH_RHPORT);
    // Note: tuh_init() already calls hcd_int_enable() internally.
    // Don't call mp_usbh_int_enable() again as it causes double interrupt
    // allocation on ESP32 (esp_intr_alloc called twice).
}

// Convert UTF-16LE bytes to UTF-8 string.
// utf16_bytes points to raw UTF-16LE data (not necessarily 2-byte aligned).
// BMP only (U+0000..U+FFFF). Surrogate pairs (U+10000+) not handled —
// extremely rare in USB string descriptors.
static void utf16le_to_utf8(const uint8_t *utf16_bytes, size_t utf16_len,
    char *utf8, size_t utf8_size) {
    size_t j = 0;
    for (size_t i = 0; i < utf16_len && j < utf8_size - 1; i++) {
        uint16_t ch = utf16_bytes[2 * i] | ((uint16_t)utf16_bytes[2 * i + 1] << 8);
        if (ch < 0x80) {
            utf8[j++] = (char)ch;
        } else if (ch < 0x800) {
            if (j + 2 > utf8_size - 1) {
                break;
            }
            utf8[j++] = 0xC0 | (ch >> 6);
            utf8[j++] = 0x80 | (ch & 0x3F);
        } else {
            if (j + 3 > utf8_size - 1) {
                break;
            }
            utf8[j++] = 0xE0 | (ch >> 12);
            utf8[j++] = 0x80 | ((ch >> 6) & 0x3F);
            utf8[j++] = 0x80 | (ch & 0x3F);
        }
    }
    utf8[j] = '\0';
}

// Fetch a single string descriptor and convert to UTF-8.
static void fetch_string_descriptor(uint8_t dev_addr, uint8_t str_index, char *out, size_t out_size) {
    out[0] = '\0';
    if (str_index == 0) {
        return;
    }
    // Buffer for raw USB string descriptor (UTF-16LE, max 255 bytes per USB spec).
    uint8_t desc_buf[128];
    if (tuh_descriptor_get_string_sync(dev_addr, str_index, 0x0409, desc_buf, sizeof(desc_buf)) == XFER_RESULT_SUCCESS) {
        uint8_t desc_len = desc_buf[0];
        if (desc_len >= 2 && desc_buf[1] == TUSB_DESC_STRING) {
            // UTF-16LE data starts at byte 2, length is (desc_len - 2) bytes.
            size_t utf16_len = (desc_len - 2) / 2;
            utf16le_to_utf8(desc_buf + 2, utf16_len, out, out_size);
        }
    }
}

void mp_usbh_fetch_device_strings(machine_usbh_device_obj_t *dev) {
    if (dev->strings_fetched || !dev->mounted) {
        return;
    }
    // Get device descriptor for string indexes and class codes.
    tusb_desc_device_t desc_device;
    if (tuh_descriptor_get_device_sync(dev->addr, &desc_device, sizeof(desc_device)) == XFER_RESULT_SUCCESS) {
        fetch_string_descriptor(dev->addr, desc_device.iManufacturer, dev->manufacturer, sizeof(dev->manufacturer));
        fetch_string_descriptor(dev->addr, desc_device.iProduct, dev->product, sizeof(dev->product));
        fetch_string_descriptor(dev->addr, desc_device.iSerialNumber, dev->serial, sizeof(dev->serial));
        dev->dev_class = desc_device.bDeviceClass;
        dev->dev_subclass = desc_device.bDeviceSubClass;
        dev->dev_protocol = desc_device.bDeviceProtocol;
    }
    dev->strings_fetched = true;
}

// Process USB host tasks.
void mp_usbh_task(void) {
    mp_obj_usb_host_t *usbh = MP_OBJ_TO_PTR(MP_STATE_VM(usbh));

    // Skip if not initialized.
    if (usbh == NULL || !usbh->initialized || !usbh->active) {
        return;
    }

    // Process TinyUSB task (non-blocking - return immediately if no events).
    // Using tuh_task_ext(0, false) instead of tuh_task() because tuh_task()
    // expands to tuh_task_ext(UINT32_MAX, false) which blocks forever waiting
    // for USB events. In MicroPython's cooperative scheduling model, we must
    // return control to the main loop to process other tasks.
    tuh_task_ext(0, false);
}

void mp_usbh_task_callback(mp_sched_node_t *node) {
    (void)node;
    mp_usbh_task();
}

TU_ATTR_FAST_FUNC void mp_usbh_schedule_task(void) {
    static mp_sched_node_t usbh_task_node;
    mp_sched_schedule_node(&usbh_task_node, mp_usbh_task_callback);
}

extern void __real_hcd_event_handler(hcd_event_t const *event, bool in_isr);

// When -Wl,--wrap=hcd_event_handler is passed to the linker, then this wrapper
// will be called and allows MicroPython to schedule the TinyUSB task when
// hcd_event_handler() is called from an ISR or task context.
TU_ATTR_FAST_FUNC void __wrap_hcd_event_handler(hcd_event_t const *event, bool in_isr) {
    __real_hcd_event_handler(event, in_isr);
    mp_usbh_schedule_task();
    if (in_isr) {
        mp_hal_wake_main_task_from_isr();
    } else {
        mp_hal_wake_main_task();
    }
}

// TinyUSB device mount callback.
// Runs inside tuh_task_ext() — no heap allocation allowed.
void tuh_mount_cb(uint8_t dev_addr) {
    mp_obj_usb_host_t *usbh = MP_OBJ_TO_PTR(MP_STATE_VM(usbh));
    if (usbh == NULL || dev_addr == 0 || dev_addr > CFG_TUH_DEVICE_MAX) {
        return;
    }

    machine_usbh_device_obj_t *device = &usbh->device_pool[dev_addr - 1];
    device->base.type = &machine_usbh_device_type;
    device->addr = dev_addr;
    tuh_vid_pid_get(dev_addr, &device->vid, &device->pid);
    device->dev_class = 0;
    device->dev_subclass = 0;
    device->dev_protocol = 0;
    device->manufacturer[0] = '\0';
    device->product[0] = '\0';
    device->serial[0] = '\0';
    device->strings_fetched = false;
    device->mounted = true;
}

// TinyUSB device unmount callback.
void tuh_umount_cb(uint8_t dev_addr) {
    mp_obj_usb_host_t *usbh = MP_OBJ_TO_PTR(MP_STATE_VM(usbh));
    if (usbh == NULL || dev_addr == 0 || dev_addr > CFG_TUH_DEVICE_MAX) {
        return;
    }

    // Mark device as unmounted.
    usbh->device_pool[dev_addr - 1].mounted = false;

    // Mark all class devices for this address as disconnected.
    for (int i = 0; i < CFG_TUH_CDC; i++) {
        if (usbh->cdc_pool[i].dev_addr == dev_addr) {
            usbh->cdc_pool[i].connected = false;
        }
    }
    for (int i = 0; i < CFG_TUH_MSC; i++) {
        if (usbh->msc_pool[i].dev_addr == dev_addr) {
            usbh->msc_pool[i].connected = false;
        }
    }
    for (int i = 0; i < CFG_TUH_HID; i++) {
        if (usbh->hid_pool[i].dev_addr == dev_addr) {
            usbh->hid_pool[i].connected = false;
        }
    }
}

// CDC mount callback.
// Runs inside tuh_task_ext() — no heap allocation allowed.
void tuh_cdc_mount_cb(uint8_t idx) {
    mp_obj_usb_host_t *usbh = MP_OBJ_TO_PTR(MP_STATE_VM(usbh));
    if (usbh == NULL || idx >= CFG_TUH_CDC) {
        return;
    }

    tuh_itf_info_t itf_info = {0};
    tuh_cdc_itf_get_info(idx, &itf_info);

    machine_usbh_cdc_obj_t *cdc = &usbh->cdc_pool[idx];
    cdc->base.type = &machine_usbh_cdc_type;
    cdc->dev_addr = itf_info.daddr;
    cdc->itf_num = idx;
    cdc->connected = true;
    cdc->irq_callback = mp_const_none;

    // Set default line coding (115200 8N1) - most CDC devices expect this.
    // Can be overridden from Python via set_line_coding().
    cdc_line_coding_t line_coding = {
        .bit_rate = 115200,
        .stop_bits = 0,    // 1 stop bit
        .parity = 0,       // no parity
        .data_bits = 8
    };
    // Fire-and-forget: if this fails the device keeps its own default line
    // coding. User can call set_line_coding() explicitly to override.
    tuh_cdc_set_line_coding(idx, &line_coding, NULL, 0);

    // Set control line state (DTR=true, RTS=false).
    uint16_t line_state = 0x01; // DTR = bit 0, RTS = bit 1
    tuh_cdc_set_control_line_state(idx, line_state, NULL, 0);
}

// CDC unmount callback.
void tuh_cdc_umount_cb(uint8_t itf_num) {
    machine_usbh_cdc_obj_t *cdc = find_cdc_by_itf(itf_num);
    if (cdc) {
        cdc->connected = false;
    }
}

// Called when data is received from CDC device.
// Uses mp_sched_schedule to defer Python callback to safe context,
// matching the HID callback pattern. Data remains buffered in TinyUSB's
// internal FIFO until read from the scheduled callback.
void tuh_cdc_rx_cb(uint8_t idx) {
    machine_usbh_cdc_obj_t *cdc = find_cdc_by_itf(idx);
    if (cdc && cdc->irq_callback != mp_const_none) {
        // mp_sched_schedule() silently drops the callback if the scheduler
        // queue is full (default 4 slots). Data stays in TinyUSB's FIFO
        // and can be read later; only the user's irq callback is missed.
        mp_sched_schedule(cdc->irq_callback, MP_OBJ_FROM_PTR(cdc));
    }
}

// TinyUSB requires this callback to be defined. Without it, CDC writes
// will silently fail because TinyUSB won't complete the write transaction.
void tuh_cdc_tx_complete_cb(uint8_t idx) {
    (void)idx;
}

// MSC mount callback.
// Runs inside tuh_task_ext() — no heap allocation allowed.
void tuh_msc_mount_cb(uint8_t dev_addr) {
    mp_obj_usb_host_t *usbh = MP_OBJ_TO_PTR(MP_STATE_VM(usbh));
    if (usbh == NULL) {
        return;
    }

    // Ensure this is a new device.
    if (find_msc_by_addr(dev_addr)) {
        return;
    }

    // Get the LUN count.
    uint8_t lun_count = tuh_msc_get_maxlun(dev_addr);

    // Find a free MSC pool slot and initialize for each LUN.
    for (uint8_t lun = 0; lun < lun_count; lun++) {
        uint32_t block_count = 0;
        uint32_t block_size = 0;
        bool ready = false;

        if (tuh_msc_ready(dev_addr)) {
            ready = true;
            block_count = tuh_msc_get_block_count(dev_addr, lun);
            block_size = tuh_msc_get_block_size(dev_addr, lun);
        }

        if (ready && block_count > 0 && block_size > 0) {
            // Find free slot in MSC pool.
            for (int i = 0; i < CFG_TUH_MSC; i++) {
                machine_usbh_msc_obj_t *msc = &usbh->msc_pool[i];
                if (!msc->connected) {
                    msc->base.type = &machine_usbh_msc_type;
                    msc->dev_addr = dev_addr;
                    msc->lun = lun;
                    msc->connected = true;
                    msc->block_size = block_size;
                    msc->block_count = block_count;
                    msc->operation_pending = false;
                    msc->operation_success = false;
                    msc->readonly = false;
                    break;
                }
            }
        }
    }
}

// MSC unmount callback.
void tuh_msc_umount_cb(uint8_t dev_addr) {
    mp_obj_usb_host_t *usbh = MP_OBJ_TO_PTR(MP_STATE_VM(usbh));
    if (usbh == NULL) {
        return;
    }
    for (int i = 0; i < CFG_TUH_MSC; i++) {
        if (usbh->msc_pool[i].dev_addr == dev_addr) {
            usbh->msc_pool[i].connected = false;
        }
    }
}

// MSC transfer complete callback - called by TinyUSB when read/write completes.
// This callback is passed to tuh_msc_read10/write10 as the completion handler.
bool mp_usbh_msc_xfer_complete(uint8_t dev_addr, tuh_msc_complete_data_t const *cb_data) {
    machine_usbh_msc_obj_t *msc = find_msc_by_addr(dev_addr);
    if (msc) {
        msc->operation_pending = false;
        // Check CSW status (0 = success).
        msc->operation_success = (cb_data->csw->status == 0);
    }
    return true;
}

// Helper function to wait for MSC operation completion.
// Returns true if operation completed successfully, false on timeout or error.
bool mp_usbh_msc_wait_complete(machine_usbh_msc_obj_t *msc, uint32_t timeout_ms) {
    uint32_t start = mp_hal_ticks_ms();

    while (msc->operation_pending) {
        if (mp_hal_ticks_ms() - start > timeout_ms) {
            return false;
        }
        // Explicitly process USB events — mp_event_wait_ms() alone may not
        // trigger the scheduled mp_usbh_task on all ports. Safe: MicroPython
        // is single-threaded cooperative, no re-entrancy with scheduled task.
        tuh_task_ext(0, false);
        mp_event_wait_ms(1);
    }

    return msc->operation_success;
}

// HID mount callback.
// Runs inside tuh_task_ext() — no heap allocation allowed.
void tuh_hid_mount_cb(uint8_t dev_addr, uint8_t instance, uint8_t const *desc_report, uint16_t desc_len) {
    mp_obj_usb_host_t *usbh = MP_OBJ_TO_PTR(MP_STATE_VM(usbh));
    if (usbh == NULL) {
        return;
    }

    uint8_t protocol = tuh_hid_interface_protocol(dev_addr, instance);

    // Minimal short-item parser: captures first Usage Page and Usage only.
    // Composite descriptors (e.g. keyboard + consumer control) will report
    // the first collection's usage.
    uint16_t usage_page = 0;
    uint16_t usage = 0;

    if (desc_report && desc_len >= 2) {
        for (uint16_t i = 0; i < desc_len;) {
            uint8_t tag = desc_report[i];
            uint8_t size = tag & 0x03;
            if (size == 3) {
                size = 4;
            }
            if (i + 1 + size > desc_len) {
                break;
            }
            if (tag == 0x05 && size >= 1) {
                usage_page = desc_report[i + 1];
            } else if (tag == 0x06 && size >= 2) {
                usage_page = desc_report[i + 1] | (desc_report[i + 2] << 8);
            } else if (tag == 0x09 && size >= 1) {
                usage = desc_report[i + 1];
                break;
            } else if (tag == 0x0A && size >= 2) {
                usage = desc_report[i + 1] | (desc_report[i + 2] << 8);
                break;
            }
            i += 1 + size;
        }
    }

    // Find a free HID pool slot.
    for (int i = 0; i < CFG_TUH_HID; i++) {
        machine_usbh_hid_obj_t *hid = &usbh->hid_pool[i];
        if (!hid->connected) {
            hid->base.type = &machine_usbh_hid_type;
            hid->dev_addr = dev_addr;
            hid->instance = instance;
            hid->protocol = protocol;
            hid->usage_page = usage_page;
            hid->usage = usage;
            hid->connected = true;
            hid->report_len = 0;
            hid->report_ready = false;
            hid->irq_callback = mp_const_none;
            break;
        }
    }

    // Start receiving reports.
    tuh_hid_receive_report(dev_addr, instance);
}

// HID unmount callback.
void tuh_hid_umount_cb(uint8_t dev_addr, uint8_t instance) {
    machine_usbh_hid_obj_t *hid = find_hid_by_addr_instance(dev_addr, instance);
    if (hid) {
        hid->connected = false;
    }
}

// HID report received callback.
void tuh_hid_report_received_cb(uint8_t dev_addr, uint8_t instance, uint8_t const *report, uint16_t len) {
    machine_usbh_hid_obj_t *hid = find_hid_by_addr_instance(dev_addr, instance);
    if (hid && len <= USBH_HID_MAX_REPORT_SIZE) {
        // Copy to pre-allocated buffer — no allocation in callback.
        memcpy(hid->report_buffer, report, len);
        hid->report_len = len;
        hid->report_ready = true;

        // Schedule callback to run in safe context (outside TinyUSB task).
        // mp_sched_schedule() silently drops the callback if the scheduler
        // queue is full. Report data stays in report_buffer and can be
        // read later; only the user's irq callback is missed.
        if (hid->irq_callback != mp_const_none) {
            mp_sched_schedule(hid->irq_callback, MP_OBJ_FROM_PTR(hid));
        }
    }

    // Continue receiving reports.
    tuh_hid_receive_report(dev_addr, instance);
}

// Enable/disable USB host interrupts.
// Ports where hcd_int_enable/disable are destructive (e.g. ESP32 where
// hcd_int_disable calls esp_intr_free) should provide their own
// mp_usbh_int_enable/disable implementations in the port code.
__attribute__((weak)) void mp_usbh_int_enable(void) {
    hcd_int_enable(BOARD_TUH_RHPORT);
}

__attribute__((weak)) void mp_usbh_int_disable(void) {
    hcd_int_disable(BOARD_TUH_RHPORT);
}

// Deinitialization function.
void mp_usbh_deinit(void) {
    mp_obj_usb_host_t *usbh = MP_OBJ_TO_PTR(MP_STATE_VM(usbh));
    if (usbh == NULL) {
        return;
    }

    if (usbh->initialized) {
        #if defined(CONFIG_IDF_TARGET_ESP32S2) || defined(CONFIG_IDF_TARGET_ESP32S3) || defined(CONFIG_IDF_TARGET_ESP32P4)
        // ESP32: Don't call tuh_deinit() — hcd_int_disable() calls
        // esp_intr_free() which is destructive. Leave interrupt allocated;
        // mp_usbh_task() checks initialized before processing.
        #else
        tuh_deinit(BOARD_TUH_RHPORT);
        #endif
    }

    usbh->active = false;
    usbh->initialized = false;

    // Clear device pools.
    for (int i = 0; i < CFG_TUH_DEVICE_MAX; i++) {
        usbh->device_pool[i].mounted = false;
    }
    for (int i = 0; i < CFG_TUH_CDC; i++) {
        usbh->cdc_pool[i].connected = false;
    }
    for (int i = 0; i < CFG_TUH_MSC; i++) {
        usbh->msc_pool[i].connected = false;
    }
    for (int i = 0; i < CFG_TUH_HID; i++) {
        usbh->hid_pool[i].connected = false;
    }

    MP_STATE_VM(usbh) = MP_OBJ_NULL;
}

#endif // MICROPY_HW_USB_HOST
