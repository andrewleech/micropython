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

// #include "mp_usbd.h"
#include "mp_usbh.h"

#if MICROPY_HW_USB_HOST

#ifndef NO_QSTR
#include "tusb.h"
#include "host/hcd.h"
#include "host/usbh.h"
#include "class/cdc/cdc_host.h"
#include "class/msc/msc_host.h"
#include "class/hid/hid_host.h"
#endif

// Helper functions to find devices by address
machine_usbh_device_obj_t *find_device_by_addr(uint8_t addr) {
    mp_obj_usb_host_t *usbh = MP_OBJ_TO_PTR(MP_STATE_VM(usbh));
    mp_obj_list_t *devices = MP_OBJ_TO_PTR(usbh->device_list);

    for (size_t i = 0; i < devices->len; i++) {
        machine_usbh_device_obj_t *device = MP_OBJ_TO_PTR(devices->items[i]);
        if (device->addr == addr) {
            return device;
        }
    }

    return NULL;
}

machine_usbh_cdc_obj_t *find_cdc_by_itf(uint8_t itf_num) {
    mp_obj_usb_host_t *usbh = MP_OBJ_TO_PTR(MP_STATE_VM(usbh));
    mp_obj_list_t *cdc_devices = MP_OBJ_TO_PTR(usbh->cdc_list);

    for (size_t i = 0; i < cdc_devices->len; i++) {
        machine_usbh_cdc_obj_t *cdc = MP_OBJ_TO_PTR(cdc_devices->items[i]);
        if (cdc->itf_num == itf_num) {
            return cdc;
        }
    }

    return NULL;
}

machine_usbh_msc_obj_t *find_msc_by_addr(uint8_t addr) {
    mp_obj_usb_host_t *usbh = MP_OBJ_TO_PTR(MP_STATE_VM(usbh));
    mp_obj_list_t *msc_devices = MP_OBJ_TO_PTR(usbh->msc_list);

    for (size_t i = 0; i < msc_devices->len; i++) {
        machine_usbh_msc_obj_t *msc = MP_OBJ_TO_PTR(msc_devices->items[i]);
        if (msc->dev_addr == addr) {
            return msc;
        }
    }

    return NULL;
}

machine_usbh_hid_obj_t *find_hid_by_addr_instance(uint8_t addr, uint8_t instance) {
    mp_obj_usb_host_t *usbh = MP_OBJ_TO_PTR(MP_STATE_VM(usbh));
    mp_obj_list_t *hid_devices = MP_OBJ_TO_PTR(usbh->hid_list);

    for (size_t i = 0; i < hid_devices->len; i++) {
        machine_usbh_hid_obj_t *hid = MP_OBJ_TO_PTR(hid_devices->items[i]);
        if (hid->dev_addr == addr && hid->instance == instance) {
            return hid;
        }
    }

    return NULL;
}

// Helper functions to check if a device is connected / mounted
bool device_mounted(uint8_t dev_addr) {
    machine_usbh_device_obj_t *device = find_device_by_addr(dev_addr);
    if (device) {
        return device->mounted;
    }
    return false;
}


// Pend an exception raise in a USBH callback to print when safe.
//
// We can't raise any exceptions out of the TinyUSB task, as it may still need
// to do some state cleanup.
//
// The requirement for this becomes very similar to
// mp_call_function_x_protected() for interrupts, but it's more restrictive: if
// the C-based USB-CDC serial port is in use, we can't print from inside a
// TinyUSB callback as it might try to recursively call into TinyUSB to flush
// the CDC port and make room. Therefore, we have to store the exception and
// print it as we exit the TinyUSB task.
//
// (Worse, a single TinyUSB task can process multiple callbacks and therefore generate
// multiple exceptions...)
static void usbh_pend_exception(mp_obj_t exception) {
    mp_obj_usb_host_t *usbh = MP_OBJ_TO_PTR(MP_STATE_VM(usbh));
    assert(usbh != NULL);
    if (usbh->num_pend_excs < MP_USBH_MAX_PEND_EXCS) {
        usbh->pend_excs[usbh->num_pend_excs] = exception;
    }
    usbh->num_pend_excs++;
}

// Call a Python function from inside a TinyUSB callback.
//
// Handles any exception using usbh_pend_exception()
static mp_obj_t usbh_callback_function_n(mp_obj_t fun, size_t n_args, const mp_obj_t *args) {
    nlr_buf_t nlr;
    if (nlr_push(&nlr) == 0) {
        mp_obj_t ret = mp_call_function_n_kw(fun, n_args, 0, args);
        nlr_pop();
        return ret;
    } else {
        usbh_pend_exception(MP_OBJ_FROM_PTR(nlr.ret_val));
        return MP_OBJ_NULL;
    }
}

// Process USB host tasks
void mp_usbh_task(void) {
    mp_obj_usb_host_t *usbh = MP_OBJ_TO_PTR(MP_STATE_VM(usbh));

    // Skip if not initialized
    if (usbh == MP_OBJ_NULL || !usbh->initialized || !usbh->active) {
        return;
    }

    // Process TinyUSB task
    tuh_task();

    // Process any pending exceptions
    mp_uint_t num_pend_excs = usbh->num_pend_excs;
    if (num_pend_excs > MP_USBH_MAX_PEND_EXCS) {
        mp_printf(&mp_plat_print, "%u additional exceptions in USB callbacks\n",
            num_pend_excs - MP_USBH_MAX_PEND_EXCS);
    }
    // Print any exceptions that were raised by Python callbacks
    // inside tuh_task(). See usbh_callback_function_n.

    // As printing exceptions to USB-CDC may recursively call mp_usbh_task(),
    // first copy out the pending data to the local stack
    mp_obj_t pend_excs[MP_USBH_MAX_PEND_EXCS];
    for (mp_uint_t i = 0; i < MIN(MP_USBH_MAX_PEND_EXCS, num_pend_excs); i++) {
        pend_excs[i] = usbh->pend_excs[i];
        usbh->pend_excs[i] = mp_const_none;
    }
    usbh->num_pend_excs = 0;

    // Now print the exceptions stored from this mp_usbh_task() call
    for (mp_uint_t i = 0; i < MIN(MP_USBH_MAX_PEND_EXCS, num_pend_excs); i++) {
        mp_obj_print_exception(&mp_plat_print, pend_excs[i]);
    }
    if (num_pend_excs > MP_USBH_MAX_PEND_EXCS) {
        mp_printf(&mp_plat_print, "%u additional exceptions in USB callbacks\n",
            num_pend_excs - MP_USBH_MAX_PEND_EXCS);
    }
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
// hcd_event_handler() is called from an ISR.
TU_ATTR_FAST_FUNC void __wrap_hcd_event_handler(hcd_event_t const *event, bool in_isr) {
    __real_hcd_event_handler(event, in_isr);
    mp_usbh_schedule_task();
    mp_hal_wake_main_task_from_isr();
}


// TinyUSB device mount callback
void tuh_mount_cb(uint8_t dev_addr) {
    // Get device information
    uint16_t vid = 0, pid = 0;
    tuh_vid_pid_get(dev_addr, &vid, &pid);

    // Since tuh_descriptor_get_device_class is not available in TinyUSB,
    // we'll set default values for now
    uint8_t dev_class = 0, dev_subclass = 0, dev_protocol = 0;

    // Create a new device object to track this device
    machine_usbh_device_obj_t *device = m_new_obj(machine_usbh_device_obj_t);
    device->base.type = &machine_usbh_device_type;
    device->addr = dev_addr;
    device->vid = vid;
    device->pid = pid;
    device->dev_class = dev_class;
    device->dev_subclass = dev_subclass;
    device->dev_protocol = dev_protocol;
    device->mounted = true;

    // Initialize string pointers (will be fetched later)
    device->manufacturer = NULL;
    device->product = NULL;
    device->serial = NULL;

    // Add device to the list
    mp_obj_usb_host_t *usbh = MP_OBJ_TO_PTR(MP_STATE_VM(usbh));
    mp_obj_list_append(usbh->device_list, MP_OBJ_FROM_PTR(device));

    // String descriptors are not available synchronously in TinyUSB
    // We'll leave them as NULL for now
    device->manufacturer = NULL;
    device->product = NULL;
    device->serial = NULL;
}

// TinyUSB device unmount callback
void tuh_umount_cb(uint8_t dev_addr) {
    mp_obj_usb_host_t *usbh = MP_OBJ_TO_PTR(MP_STATE_VM(usbh));

    // Find and mark the device as unmounted
    machine_usbh_device_obj_t *device = find_device_by_addr(dev_addr);
    if (device) {
        device->mounted = false;

        // Mark any CDC devices as disconnected
        mp_obj_list_t *cdc_list = MP_OBJ_TO_PTR(usbh->cdc_list);
        for (size_t i = 0; i < cdc_list->len; i++) {
            machine_usbh_cdc_obj_t *cdc = MP_OBJ_TO_PTR(cdc_list->items[i]);
            if (cdc->dev_addr == dev_addr) {
                cdc->connected = false;
            }
        }

        // Mark any MSC devices as disconnected
        mp_obj_list_t *msc_list = MP_OBJ_TO_PTR(usbh->msc_list);
        for (size_t i = 0; i < msc_list->len; i++) {
            machine_usbh_msc_obj_t *msc = MP_OBJ_TO_PTR(msc_list->items[i]);
            if (msc->dev_addr == dev_addr) {
                msc->connected = false;
            }
        }

        // Mark any HID devices as disconnected
        mp_obj_list_t *hid_list = MP_OBJ_TO_PTR(usbh->hid_list);
        for (size_t i = 0; i < hid_list->len; i++) {
            machine_usbh_hid_obj_t *hid = MP_OBJ_TO_PTR(hid_list->items[i]);
            if (hid->dev_addr == dev_addr) {
                hid->connected = false;
            }
        }
    }
}

// CDC mount callback
void tuh_cdc_mount_cb(uint8_t idx) {
    // Get details about the interface
    tuh_itf_info_t itf_info = {0};
    tuh_cdc_itf_get_info(idx, &itf_info);

    // Create a new CDC device object
    machine_usbh_cdc_obj_t *cdc = m_new_obj(machine_usbh_cdc_obj_t);
    cdc->base.type = &machine_usbh_cdc_type;
    cdc->dev_addr = itf_info.daddr;
    cdc->itf_num = idx;
    cdc->connected = true;
    cdc->irq_callback = mp_const_none;

    // Add to the CDC device list
    mp_obj_usb_host_t *usbh = MP_OBJ_TO_PTR(MP_STATE_VM(usbh));
    mp_obj_list_append(usbh->cdc_list, MP_OBJ_FROM_PTR(cdc));

    // // Start receiving data
    // tuh_cdc_receive(itf_num, NULL, 0, true);
}

// CDC unmount callback
void tuh_cdc_umount_cb(uint8_t itf_num) {
    // Find the CDC device
    machine_usbh_cdc_obj_t *cdc = find_cdc_by_itf(itf_num);
    if (cdc) {
        cdc->connected = false;
    }
}


// Called when data is received from CDC device
void tuh_cdc_rx_cb(uint8_t idx) {
    // Find the CDC device
    machine_usbh_cdc_obj_t *cdc = find_cdc_by_itf(idx);
    if (cdc) {
        // Run callback if one is registered.
        if (cdc->irq_callback != mp_const_none) {
            usbh_callback_function_n(
                cdc->irq_callback,
                1,
                (mp_obj_t []) {MP_OBJ_FROM_PTR(cdc)}
                );
        }
    }
}

// MSC mount callback
void tuh_msc_mount_cb(uint8_t dev_addr) {
    // Ensure this is a new device
    if (find_msc_by_addr(dev_addr)) {
        return;
    }

    // Get the LUN count
    uint8_t lun_count = tuh_msc_get_maxlun(dev_addr);

    // Create MSC device object for each LUN
    for (uint8_t lun = 0; lun < lun_count; lun++) {
        // Get capacity
        uint32_t block_count = 0;
        uint32_t block_size = 0;
        bool ready = false;

        if (tuh_msc_ready(dev_addr)) {
            ready = true;
            block_count = tuh_msc_get_block_count(dev_addr, lun);
            block_size = tuh_msc_get_block_size(dev_addr, lun);
        }

        // Only create if ready and has valid capacity
        if (ready && block_count > 0 && block_size > 0) {
            // Create MSC device object
            machine_usbh_msc_obj_t *msc = m_new_obj(machine_usbh_msc_obj_t);
            msc->base.type = &machine_usbh_msc_type;
            msc->dev_addr = dev_addr;
            msc->lun = lun;
            msc->connected = true;
            msc->block_size = block_size;
            msc->block_count = block_count;
            msc->block_cache = m_new(uint8_t, block_size);
            msc->block_cache_addr = -1;

            // Check write protection
            msc->readonly = false;  // Assume writable by default

            // Add to MSC device list
            mp_obj_usb_host_t *usbh = MP_OBJ_TO_PTR(MP_STATE_VM(usbh));
            mp_obj_list_append(usbh->msc_list, MP_OBJ_FROM_PTR(msc));
        }
    }
}

// MSC unmount callback
void tuh_msc_umount_cb(uint8_t dev_addr) {
    // Mark MSC device as disconnected
    machine_usbh_msc_obj_t *msc = find_msc_by_addr(dev_addr);
    if (msc) {
        msc->connected = false;
    }
}


// HID mount callback
void tuh_hid_mount_cb(uint8_t dev_addr, uint8_t instance, uint8_t const *desc_report, uint16_t desc_len) {
    // Get HID info
    uint8_t protocol = tuh_hid_interface_protocol(dev_addr, instance);

    // Get usage page and usage
    uint16_t usage_page = 0;
    uint16_t usage = 0;

    if (desc_report && desc_len > 0) {
        // Basic parsing for usage page and usage from report descriptor
        for (uint16_t i = 0; i < desc_len - 2; i++) {
            if (desc_report[i] == 0x05) {
                // Usage Page item (short)
                usage_page = desc_report[i + 1];
            } else if (desc_report[i] == 0x06) {
                // Usage Page item (long)
                usage_page = desc_report[i + 1] | (desc_report[i + 2] << 8);
            } else if (desc_report[i] == 0x09) {
                // Usage item (short)
                usage = desc_report[i + 1];
                break;  // Use the first usage found
            } else if (desc_report[i] == 0x0A) {
                // Usage item (long)
                usage = desc_report[i + 1] | (desc_report[i + 2] << 8);
                break;  // Use the first usage found
            }
        }
    }

    // Create HID device object
    machine_usbh_hid_obj_t *hid = m_new_obj(machine_usbh_hid_obj_t);
    hid->base.type = &machine_usbh_hid_type;
    hid->dev_addr = dev_addr;
    hid->instance = instance;
    hid->protocol = protocol;
    hid->usage_page = usage_page;
    hid->usage = usage;
    hid->connected = true;
    hid->latest_report = MP_OBJ_NULL;
    hid->irq_callback = mp_const_none;

    // Add to HID device list
    mp_obj_usb_host_t *usbh = MP_OBJ_TO_PTR(MP_STATE_VM(usbh));
    mp_obj_list_append(usbh->hid_list, MP_OBJ_FROM_PTR(hid));

    // Start receiving reports
    tuh_hid_receive_report(dev_addr, instance);
}

// HID unmount callback
void tuh_hid_umount_cb(uint8_t dev_addr, uint8_t instance) {
    // Mark HID device as disconnected
    machine_usbh_hid_obj_t *hid = find_hid_by_addr_instance(dev_addr, instance);
    if (hid) {
        hid->connected = false;
    }
}

// HID report received callback
void tuh_hid_report_received_cb(uint8_t dev_addr, uint8_t instance, uint8_t const *report, uint16_t len) {
    // Find the HID device
    machine_usbh_hid_obj_t *hid = find_hid_by_addr_instance(dev_addr, instance);
    if (hid) {
        // Store the report
        if (len <= USBH_HID_MAX_REPORT_SIZE) {
            hid->latest_report = mp_obj_new_bytes(report, len);

            // Run callback if one is registered.
            if (hid->irq_callback != mp_const_none) {
                usbh_callback_function_n(
                    hid->irq_callback,
                    2,
                    (mp_obj_t []) {MP_OBJ_FROM_PTR(hid), hid->latest_report}
                    );
            }
        }

        // Continue receiving reports
        tuh_hid_receive_report(dev_addr, instance);
    }
}

MP_REGISTER_ROOT_POINTER(mp_obj_t usbh);

#endif // MICROPY_HW_USB_HOST
