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

#include "py/mpconfig.h"

#if MICROPY_HW_USB_HOST

#include "py/obj.h"
#include "py/runtime.h"
#include "py/objstr.h"
#include "py/mperrno.h"
#include "py/stream.h"
#include "shared/runtime/mpirq.h"
#include "extmod/vfs.h"

#include "shared/tinyusb/mp_usbh.h"

// For CDC stream protocol
static mp_uint_t machine_usbh_cdc_read_method(mp_obj_t self_in, void *buf_in, mp_uint_t size, int *errcode);
static mp_uint_t machine_usbh_cdc_write_method(mp_obj_t self_in, const void *buf_in, mp_uint_t size, int *errcode);
static mp_uint_t machine_usbh_cdc_ioctl_method(mp_obj_t self_in, mp_uint_t request, uintptr_t arg, int *errcode);

static const mp_stream_p_t machine_usbh_cdc_stream_p = {
    .read = machine_usbh_cdc_read_method,
    .write = machine_usbh_cdc_write_method,
    .ioctl = machine_usbh_cdc_ioctl_method,
    .is_text = false,
};

/******************************************************************************/
// MicroPython bindings for USBHost

// Print function for USBHost
static void machine_usb_host_print(const mp_print_t *print, mp_obj_t self_in, mp_print_kind_t kind) {
    mp_obj_usb_host_t *self = MP_OBJ_TO_PTR(self_in);
    mp_obj_list_t *devices = MP_OBJ_TO_PTR(self->device_list);
    mp_printf(print, "<USBHost devices: %d>", devices->len);
}

// Create a new USBHost object
static mp_obj_t machine_usb_host_make_new(const mp_obj_type_t *type, size_t n_args, size_t n_kw, const mp_obj_t *args) {
    // Parse arguments (none for now)
    mp_arg_check_num(n_args, n_kw, 0, 0, false);

    // Create the self object
    if (MP_STATE_VM(usbh) == MP_OBJ_NULL) {
        mp_obj_usb_host_t *self = mp_obj_malloc(mp_obj_usb_host_t, type);

        // Create lists to track devices
        self->device_list = mp_obj_new_list(0, NULL);
        self->cdc_list = mp_obj_new_list(0, NULL);
        self->msc_list = mp_obj_new_list(0, NULL);
        self->hid_list = mp_obj_new_list(0, NULL);
        self->initialized = false;
        self->active = false;

        // Initialize string storage
        for (int i = 0; i < USBH_MAX_DEVICES; i++) {
            self->manufacturer_str[i][0] = '\0';
            self->product_str[i][0] = '\0';
            self->serial_str[i][0] = '\0';
        }

        self->num_pend_excs = 0;
        MP_STATE_VM(usbh) = MP_OBJ_FROM_PTR(self);
    }

    return MP_STATE_VM(usbh);
}

// Method to get the list of devices
static mp_obj_t machine_usb_host_devices(mp_obj_t self_in) {
    mp_obj_usb_host_t *self = MP_OBJ_TO_PTR(self_in);
    return self->device_list;
}
static MP_DEFINE_CONST_FUN_OBJ_1(machine_usb_host_devices_obj, machine_usb_host_devices);

// Method to get the list of CDC devices
static mp_obj_t machine_usb_host_cdc_devices(mp_obj_t self_in) {
    mp_obj_usb_host_t *self = MP_OBJ_TO_PTR(self_in);
    return self->cdc_list;
}
static MP_DEFINE_CONST_FUN_OBJ_1(machine_usb_host_cdc_devices_obj, machine_usb_host_cdc_devices);

// Method to get the list of MSC devices
static mp_obj_t machine_usb_host_msc_devices(mp_obj_t self_in) {
    mp_obj_usb_host_t *self = MP_OBJ_TO_PTR(self_in);
    return self->msc_list;
}
static MP_DEFINE_CONST_FUN_OBJ_1(machine_usb_host_msc_devices_obj, machine_usb_host_msc_devices);

// Method to get the list of HID devices
static mp_obj_t machine_usb_host_hid_devices(mp_obj_t self_in) {
    mp_obj_usb_host_t *self = MP_OBJ_TO_PTR(self_in);
    return self->hid_list;
}
static MP_DEFINE_CONST_FUN_OBJ_1(machine_usb_host_hid_devices_obj, machine_usb_host_hid_devices);

// Method to initialize/get USB host state
static mp_obj_t machine_usb_host_active(size_t n_args, const mp_obj_t *args) {
    mp_obj_usb_host_t *self = MP_OBJ_TO_PTR(args[0]);

    if (n_args > 1) {
        bool active = mp_obj_is_true(args[1]);
        if (active && !self->active) {
            // Initialize if not already
            if (!self->initialized) {
                mp_usbh_init_tuh();
                self->initialized = true;
            }
            self->active = true;
        } else if (!active && self->active) {
            self->active = false;
        }
    }

    return mp_obj_new_bool(self->active);
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(machine_usb_host_active_obj, 1, 2, machine_usb_host_active);

// Local dict for USBHost
static const mp_rom_map_elem_t machine_usb_host_locals_dict_table[] = {
    { MP_ROM_QSTR(MP_QSTR_active), MP_ROM_PTR(&machine_usb_host_active_obj) },
    { MP_ROM_QSTR(MP_QSTR_devices), MP_ROM_PTR(&machine_usb_host_devices_obj) },
    { MP_ROM_QSTR(MP_QSTR_cdc_devices), MP_ROM_PTR(&machine_usb_host_cdc_devices_obj) },
    { MP_ROM_QSTR(MP_QSTR_msc_devices), MP_ROM_PTR(&machine_usb_host_msc_devices_obj) },
    { MP_ROM_QSTR(MP_QSTR_hid_devices), MP_ROM_PTR(&machine_usb_host_hid_devices_obj) },
};
static MP_DEFINE_CONST_DICT(machine_usb_host_locals_dict, machine_usb_host_locals_dict_table);

MP_DEFINE_CONST_OBJ_TYPE(
    machine_usb_host_type,
    MP_QSTR_USBHost,
    MP_TYPE_FLAG_NONE,
    make_new, machine_usb_host_make_new,
    print, machine_usb_host_print,
    locals_dict, &machine_usb_host_locals_dict
    );

/******************************************************************************/
// MicroPython bindings for USBH_Device

// Print function for USBH_Device
static void machine_usbh_device_print(const mp_print_t *print, mp_obj_t self_in, mp_print_kind_t kind) {
    machine_usbh_device_obj_t *self = MP_OBJ_TO_PTR(self_in);
    mp_printf(print, "USBH_Device(addr=%u, vid=0x%04x, pid=0x%04x, class=%u)",
        self->addr, self->vid, self->pid, self->dev_class);
}

// Method to get VID
static mp_obj_t machine_usbh_device_get_vid(mp_obj_t self_in) {
    machine_usbh_device_obj_t *self = MP_OBJ_TO_PTR(self_in);
    return MP_OBJ_NEW_SMALL_INT(self->vid);
}
static MP_DEFINE_CONST_FUN_OBJ_1(machine_usbh_device_get_vid_obj, machine_usbh_device_get_vid);

// Method to get PID
static mp_obj_t machine_usbh_device_get_pid(mp_obj_t self_in) {
    machine_usbh_device_obj_t *self = MP_OBJ_TO_PTR(self_in);
    return MP_OBJ_NEW_SMALL_INT(self->pid);
}
static MP_DEFINE_CONST_FUN_OBJ_1(machine_usbh_device_get_pid_obj, machine_usbh_device_get_pid);

// Method to get manufacturer string
static mp_obj_t machine_usbh_device_get_manufacturer(mp_obj_t self_in) {
    machine_usbh_device_obj_t *self = MP_OBJ_TO_PTR(self_in);
    if (self->manufacturer) {
        return mp_obj_new_str(self->manufacturer, strlen(self->manufacturer));
    } else {
        return mp_const_none;
    }
}
static MP_DEFINE_CONST_FUN_OBJ_1(machine_usbh_device_get_manufacturer_obj, machine_usbh_device_get_manufacturer);

// Method to get product string
static mp_obj_t machine_usbh_device_get_product(mp_obj_t self_in) {
    machine_usbh_device_obj_t *self = MP_OBJ_TO_PTR(self_in);
    if (self->product) {
        return mp_obj_new_str(self->product, strlen(self->product));
    } else {
        return mp_const_none;
    }
}
static MP_DEFINE_CONST_FUN_OBJ_1(machine_usbh_device_get_product_obj, machine_usbh_device_get_product);

// Method to get serial string
static mp_obj_t machine_usbh_device_get_serial(mp_obj_t self_in) {
    machine_usbh_device_obj_t *self = MP_OBJ_TO_PTR(self_in);
    if (self->serial) {
        return mp_obj_new_str(self->serial, strlen(self->serial));
    } else {
        return mp_const_none;
    }
}
static MP_DEFINE_CONST_FUN_OBJ_1(machine_usbh_device_get_serial_obj, machine_usbh_device_get_serial);

// Local dict for USBH_Device
static const mp_rom_map_elem_t machine_usbh_device_locals_dict_table[] = {
    { MP_ROM_QSTR(MP_QSTR_vid), MP_ROM_PTR(&machine_usbh_device_get_vid_obj) },
    { MP_ROM_QSTR(MP_QSTR_pid), MP_ROM_PTR(&machine_usbh_device_get_pid_obj) },
    { MP_ROM_QSTR(MP_QSTR_manufacturer), MP_ROM_PTR(&machine_usbh_device_get_manufacturer_obj) },
    { MP_ROM_QSTR(MP_QSTR_product), MP_ROM_PTR(&machine_usbh_device_get_product_obj) },
    { MP_ROM_QSTR(MP_QSTR_serial), MP_ROM_PTR(&machine_usbh_device_get_serial_obj) },
};
static MP_DEFINE_CONST_DICT(machine_usbh_device_locals_dict, machine_usbh_device_locals_dict_table);

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
        self->dev_addr, self->itf_num);
}

// Method to check if connected
static mp_obj_t machine_usbh_cdc_is_connected(mp_obj_t self_in) {
    machine_usbh_cdc_obj_t *self = MP_OBJ_TO_PTR(self_in);
    return mp_obj_new_bool(self->connected && device_mounted(self->dev_addr));
}
static MP_DEFINE_CONST_FUN_OBJ_1(machine_usbh_cdc_is_connected_obj, machine_usbh_cdc_is_connected);

// Method to check if data is available
static mp_obj_t machine_usbh_cdc_any(mp_obj_t self_in) {
    machine_usbh_cdc_obj_t *self = MP_OBJ_TO_PTR(self_in);
    if (!self->connected || !device_mounted(self->dev_addr)) {
        return MP_OBJ_NEW_SMALL_INT(0);
    }

    uint32_t available = tuh_cdc_read_available(self->itf_num);
    return MP_OBJ_NEW_SMALL_INT(available);
}
static MP_DEFINE_CONST_FUN_OBJ_1(machine_usbh_cdc_any_obj, machine_usbh_cdc_any);

// Method to read data (non-blocking)
static mp_obj_t machine_usbh_cdc_read(size_t n_args, const mp_obj_t *args) {
    machine_usbh_cdc_obj_t *self = MP_OBJ_TO_PTR(args[0]);
    if (!self->connected || !device_mounted(self->dev_addr)) {
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
    if (!self->connected || !device_mounted(self->dev_addr)) {
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

// Method for setting up IRQ handler
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

        // Set the callback
        self->irq_callback = handler;
    }

    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_KW(machine_usbh_cdc_irq_obj, 1, machine_usbh_cdc_irq);

// Stream protocol implementation
static mp_uint_t machine_usbh_cdc_read_method(mp_obj_t self_in, void *buf_in, mp_uint_t size, int *errcode) {
    machine_usbh_cdc_obj_t *self = MP_OBJ_TO_PTR(self_in);
    if (!self->connected || !device_mounted(self->dev_addr)) {
        *errcode = MP_ENODEV;
        return MP_STREAM_ERROR;
    }

    // Read from the CDC device
    uint32_t count = tuh_cdc_read(self->itf_num, buf_in, size);
    return count;
}

static mp_uint_t machine_usbh_cdc_write_method(mp_obj_t self_in, const void *buf_in, mp_uint_t size, int *errcode) {
    machine_usbh_cdc_obj_t *self = MP_OBJ_TO_PTR(self_in);
    if (!self->connected || !device_mounted(self->dev_addr)) {
        *errcode = MP_ENODEV;
        return MP_STREAM_ERROR;
    }

    // Write to the CDC device
    uint32_t count = tuh_cdc_write(self->itf_num, buf_in, size);
    return count;
}

static mp_uint_t machine_usbh_cdc_ioctl_method(mp_obj_t self_in, mp_uint_t request, uintptr_t arg, int *errcode) {
    machine_usbh_cdc_obj_t *self = MP_OBJ_TO_PTR(self_in);
    mp_uint_t ret;

    if (request == MP_STREAM_POLL) {
        ret = 0;
        if (!self->connected || !device_mounted(self->dev_addr)) {
            ret |= MP_STREAM_POLL_NVAL;
        } else {
            uint32_t available = tuh_cdc_read_available(self->itf_num);
            if (available > 0) {
                ret |= MP_STREAM_POLL_RD;
            }

            // Assume we can always write
            ret |= MP_STREAM_POLL_WR;
        }
        return ret;
    }

    *errcode = MP_EINVAL;
    return MP_STREAM_ERROR;
}

// Local dict for USBH_CDC type
static const mp_rom_map_elem_t machine_usbh_cdc_locals_dict_table[] = {
    { MP_ROM_QSTR(MP_QSTR_is_connected), MP_ROM_PTR(&machine_usbh_cdc_is_connected_obj) },
    { MP_ROM_QSTR(MP_QSTR_any), MP_ROM_PTR(&machine_usbh_cdc_any_obj) },
    { MP_ROM_QSTR(MP_QSTR_read), MP_ROM_PTR(&machine_usbh_cdc_read_obj) },
    { MP_ROM_QSTR(MP_QSTR_write), MP_ROM_PTR(&machine_usbh_cdc_write_obj) },
    { MP_ROM_QSTR(MP_QSTR_irq), MP_ROM_PTR(&machine_usbh_cdc_irq_obj) },

    // Constants
    { MP_ROM_QSTR(MP_QSTR_IRQ_RX), MP_ROM_INT(USBH_CDC_IRQ_RX) },
};
static MP_DEFINE_CONST_DICT(machine_usbh_cdc_locals_dict, machine_usbh_cdc_locals_dict_table);

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
// MicroPython bindings for USBH_MSC

// Print function for MSC device
static void machine_usbh_msc_print(const mp_print_t *print, mp_obj_t self_in, mp_print_kind_t kind) {
    machine_usbh_msc_obj_t *self = MP_OBJ_TO_PTR(self_in);
    mp_printf(print, "USBH_MSC(addr=%u, lun=%u, block_size=%u, block_count=%u, readonly=%u)",
        self->dev_addr, self->lun, self->block_size, self->block_count, self->readonly);
}

// Method to check if connected
static mp_obj_t machine_usbh_msc_is_connected(mp_obj_t self_in) {
    machine_usbh_msc_obj_t *self = MP_OBJ_TO_PTR(self_in);
    return mp_obj_new_bool(self->connected && device_mounted(self->dev_addr));
}
static MP_DEFINE_CONST_FUN_OBJ_1(machine_usbh_msc_is_connected_obj, machine_usbh_msc_is_connected);

// Method to read blocks for block protocol
static mp_obj_t machine_usbh_msc_readblocks(size_t n_args, const mp_obj_t *args) {
    machine_usbh_msc_obj_t *self = MP_OBJ_TO_PTR(args[0]);

    if (!self->connected || !device_mounted(self->dev_addr)) {
        mp_raise_OSError(MP_ENODEV);
    }

    mp_buffer_info_t bufinfo;
    mp_get_buffer_raise(args[2], &bufinfo, MP_BUFFER_WRITE);

    mp_int_t block_num = mp_obj_get_int(args[1]);

    // Handle offset parameter if provided
    mp_int_t offset = 0;
    if (n_args >= 4) {
        offset = mp_obj_get_int(args[3]);
    }

    // Check offset is within block size
    if (offset < 0 || offset >= self->block_size) {
        mp_raise_ValueError(MP_ERROR_TEXT("offset is invalid"));
    }

    // Check size is valid for reading from the block
    if (bufinfo.len > (self->block_size - offset)) {
        mp_raise_ValueError(MP_ERROR_TEXT("buffer length exceeds block size - offset"));
    }

    if (self->busy) {
        mp_raise_OSError(MP_EBUSY);
    }
    self->busy = true;

    // Read from MSC device
    bool success = tuh_msc_read10(self->dev_addr, self->lun, bufinfo.buf, block_num, bufinfo.len / self->block_size, NULL, 0);

    self->busy = false;

    if (!success) {
        mp_raise_OSError(MP_EIO);
    }

    return mp_const_none;
}
// For extended protocol with offset support
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(machine_usbh_msc_readblocks_obj, 3, 4, machine_usbh_msc_readblocks);

// Method to write blocks for block protocol
static mp_obj_t machine_usbh_msc_writeblocks(size_t n_args, const mp_obj_t *args) {
    machine_usbh_msc_obj_t *self = MP_OBJ_TO_PTR(args[0]);

    if (!self->connected || !device_mounted(self->dev_addr)) {
        mp_raise_OSError(MP_ENODEV);
    }

    if (self->readonly) {
        mp_raise_OSError(MP_EROFS);
    }

    mp_buffer_info_t bufinfo;
    mp_get_buffer_raise(args[2], &bufinfo, MP_BUFFER_READ);

    mp_int_t block_num = mp_obj_get_int(args[1]);

    // NOTE: Currently we don't use the offset parameter for simplicity
    // This means only the basic protocol is supported, not littlefs

    if (self->busy) {
        mp_raise_OSError(MP_EINPROGRESS);
    }

    self->busy = true;

    // Write to MSC device
    bool success = tuh_msc_write10(self->dev_addr, self->lun, bufinfo.buf, block_num, bufinfo.len / self->block_size, NULL, 0);

    self->busy = false;

    if (!success) {
        mp_raise_OSError(MP_EIO);
    }

    return mp_const_none;
}
// Basic protocol for compatibility
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(machine_usbh_msc_writeblocks_obj, 3, 3, machine_usbh_msc_writeblocks);

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
    mp_printf(print, "USBH_HID(addr=%u, instance=%u, protocol=%u)",
        self->dev_addr, self->instance, self->protocol);
}

// Method to check if connected
static mp_obj_t machine_usbh_hid_is_connected(mp_obj_t self_in) {
    machine_usbh_hid_obj_t *self = MP_OBJ_TO_PTR(self_in);
    return mp_obj_new_bool(self->connected && device_mounted(self->dev_addr));
}
static MP_DEFINE_CONST_FUN_OBJ_1(machine_usbh_hid_is_connected_obj, machine_usbh_hid_is_connected);

// Method to get latest report
static mp_obj_t machine_usbh_hid_get_report(mp_obj_t self_in) {
    machine_usbh_hid_obj_t *self = MP_OBJ_TO_PTR(self_in);
    if (!self->connected || !device_mounted(self->dev_addr)) {
        mp_raise_OSError(MP_ENODEV);
    }

    if (self->latest_report != MP_OBJ_NULL) {
        return self->latest_report;
    } else {
        return mp_const_none;
    }
}
static MP_DEFINE_CONST_FUN_OBJ_1(machine_usbh_hid_get_report_obj, machine_usbh_hid_get_report);

// Method to get protocol
static mp_obj_t machine_usbh_hid_get_protocol(mp_obj_t self_in) {
    machine_usbh_hid_obj_t *self = MP_OBJ_TO_PTR(self_in);
    return MP_OBJ_NEW_SMALL_INT(self->protocol);
}
static MP_DEFINE_CONST_FUN_OBJ_1(machine_usbh_hid_get_protocol_obj, machine_usbh_hid_get_protocol);

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

        // Set the callback
        self->irq_callback = handler;
    }

    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_KW(machine_usbh_hid_irq_obj, 1, machine_usbh_hid_irq);

// Method to request a report from the device
static mp_obj_t machine_usbh_hid_request_report(mp_obj_t self_in) {
    machine_usbh_hid_obj_t *self = MP_OBJ_TO_PTR(self_in);
    if (!self->connected || !device_mounted(self->dev_addr)) {
        mp_raise_OSError(MP_ENODEV);
    }

    // Request a report from the device
    bool success = tuh_hid_receive_report(self->dev_addr, self->instance);
    return mp_obj_new_bool(success);
}
static MP_DEFINE_CONST_FUN_OBJ_1(machine_usbh_hid_request_report_obj, machine_usbh_hid_request_report);

// Method to send a report to the device (for output reports)
static mp_obj_t machine_usbh_hid_send_report(mp_obj_t self_in, mp_obj_t report_in) {
    machine_usbh_hid_obj_t *self = MP_OBJ_TO_PTR(self_in);
    if (!self->connected || !device_mounted(self->dev_addr)) {
        mp_raise_OSError(MP_ENODEV);
    }

    // Get the report data
    mp_buffer_info_t bufinfo;
    mp_get_buffer_raise(report_in, &bufinfo, MP_BUFFER_READ);

    if (bufinfo.len > USBH_HID_MAX_REPORT_SIZE) {
        mp_raise_ValueError(MP_ERROR_TEXT("report too large"));
    }

    // Send the report
    bool success = tuh_hid_send_report(self->dev_addr, self->instance, 0, bufinfo.buf, bufinfo.len);
    return mp_obj_new_bool(success);
}
static MP_DEFINE_CONST_FUN_OBJ_2(machine_usbh_hid_send_report_obj, machine_usbh_hid_send_report);

// Local dict for USBH_HID type
static const mp_rom_map_elem_t machine_usbh_hid_locals_dict_table[] = {
    { MP_ROM_QSTR(MP_QSTR_is_connected), MP_ROM_PTR(&machine_usbh_hid_is_connected_obj) },
    { MP_ROM_QSTR(MP_QSTR_protocol), MP_ROM_PTR(&machine_usbh_hid_get_protocol_obj) },
    { MP_ROM_QSTR(MP_QSTR_get_report), MP_ROM_PTR(&machine_usbh_hid_get_report_obj) },
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

MP_DEFINE_CONST_OBJ_TYPE(
    machine_usbh_hid_type,
    MP_QSTR_USBH_HID,
    MP_TYPE_FLAG_NONE,
    print, machine_usbh_hid_print,
    locals_dict, &machine_usbh_hid_locals_dict
    );

#endif // MICROPY_HW_USB_HOST
