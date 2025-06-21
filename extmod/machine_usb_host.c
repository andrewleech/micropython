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

// Method to get/set active state
static mp_obj_t machine_usb_host_active(size_t n_args, const mp_obj_t *args) {
    mp_obj_usb_host_t *self = MP_OBJ_TO_PTR(args[0]);

    if (n_args == 1) {
        // Return current state
        return mp_obj_new_bool(self->active);
    } else {
        // Set state
        bool new_active = mp_obj_is_true(args[1]);

        if (new_active && !self->initialized) {
            // Initialize TinyUSB host
            mp_usbh_init_tuh();
            self->initialized = true;
        }

        self->active = new_active;
        return mp_const_none;
    }
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(machine_usb_host_active_obj, 1, 2, machine_usb_host_active);

// Method to get the list of devices
static mp_obj_t machine_usb_host_devices(mp_obj_t self_in) {
    mp_obj_usb_host_t *self = MP_OBJ_TO_PTR(self_in);
    return self->device_list;
}
static MP_DEFINE_CONST_FUN_OBJ_1(machine_usb_host_devices_obj, machine_usb_host_devices);

// Method to get CDC devices
static mp_obj_t machine_usb_host_cdc_devices(mp_obj_t self_in) {
    mp_obj_usb_host_t *self = MP_OBJ_TO_PTR(self_in);
    return self->cdc_list;
}
static MP_DEFINE_CONST_FUN_OBJ_1(machine_usb_host_cdc_devices_obj, machine_usb_host_cdc_devices);

// Method to get MSC devices
static mp_obj_t machine_usb_host_msc_devices(mp_obj_t self_in) {
    mp_obj_usb_host_t *self = MP_OBJ_TO_PTR(self_in);
    return self->msc_list;
}
static MP_DEFINE_CONST_FUN_OBJ_1(machine_usb_host_msc_devices_obj, machine_usb_host_msc_devices);

// Method to get HID devices
static mp_obj_t machine_usb_host_hid_devices(mp_obj_t self_in) {
    mp_obj_usb_host_t *self = MP_OBJ_TO_PTR(self_in);
    return self->hid_list;
}
static MP_DEFINE_CONST_FUN_OBJ_1(machine_usb_host_hid_devices_obj, machine_usb_host_hid_devices);

// USBHost class methods table
static const mp_rom_map_elem_t machine_usb_host_locals_dict_table[] = {
    { MP_ROM_QSTR(MP_QSTR_active), MP_ROM_PTR(&machine_usb_host_active_obj) },
    { MP_ROM_QSTR(MP_QSTR_devices), MP_ROM_PTR(&machine_usb_host_devices_obj) },
    { MP_ROM_QSTR(MP_QSTR_cdc_devices), MP_ROM_PTR(&machine_usb_host_cdc_devices_obj) },
    { MP_ROM_QSTR(MP_QSTR_msc_devices), MP_ROM_PTR(&machine_usb_host_msc_devices_obj) },
    { MP_ROM_QSTR(MP_QSTR_hid_devices), MP_ROM_PTR(&machine_usb_host_hid_devices_obj) },
};
static MP_DEFINE_CONST_DICT(machine_usb_host_locals_dict, machine_usb_host_locals_dict_table);

// Define the USBHost type
MP_DEFINE_CONST_OBJ_TYPE(
    machine_usb_host_type,
    MP_QSTR_USBHost,
    MP_TYPE_FLAG_NONE,
    make_new, machine_usb_host_make_new,
    print, machine_usb_host_print,
    locals_dict, &machine_usb_host_locals_dict
    );

/******************************************************************************/
// MicroPython bindings for USBDevice

// Print function for USBDevice
static void machine_usbh_device_print(const mp_print_t *print, mp_obj_t self_in, mp_print_kind_t kind) {
    machine_usbh_device_obj_t *self = MP_OBJ_TO_PTR(self_in);
    mp_printf(print, "<USBDevice addr=%d VID=%04x PID=%04x>", self->addr, self->vid, self->pid);
}

// Get VID attribute
static mp_obj_t machine_usbh_device_vid_get(mp_obj_t self_in) {
    machine_usbh_device_obj_t *self = MP_OBJ_TO_PTR(self_in);
    return mp_obj_new_int(self->vid);
}
static MP_DEFINE_CONST_FUN_OBJ_1(machine_usbh_device_vid_obj, machine_usbh_device_vid_get);

// Get PID attribute
static mp_obj_t machine_usbh_device_pid_get(mp_obj_t self_in) {
    machine_usbh_device_obj_t *self = MP_OBJ_TO_PTR(self_in);
    return mp_obj_new_int(self->pid);
}
static MP_DEFINE_CONST_FUN_OBJ_1(machine_usbh_device_pid_obj, machine_usbh_device_pid_get);

// Get manufacturer attribute
static mp_obj_t machine_usbh_device_manufacturer_get(mp_obj_t self_in) {
    machine_usbh_device_obj_t *self = MP_OBJ_TO_PTR(self_in);
    if (self->manufacturer) {
        return mp_obj_new_str(self->manufacturer, strlen(self->manufacturer));
    }
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(machine_usbh_device_manufacturer_obj, machine_usbh_device_manufacturer_get);

// Get product attribute
static mp_obj_t machine_usbh_device_product_get(mp_obj_t self_in) {
    machine_usbh_device_obj_t *self = MP_OBJ_TO_PTR(self_in);
    if (self->product) {
        return mp_obj_new_str(self->product, strlen(self->product));
    }
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(machine_usbh_device_product_obj, machine_usbh_device_product_get);

// Get serial attribute
static mp_obj_t machine_usbh_device_serial_get(mp_obj_t self_in) {
    machine_usbh_device_obj_t *self = MP_OBJ_TO_PTR(self_in);
    if (self->serial) {
        return mp_obj_new_str(self->serial, strlen(self->serial));
    }
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(machine_usbh_device_serial_obj, machine_usbh_device_serial_get);

// USBDevice class methods table
static const mp_rom_map_elem_t machine_usbh_device_locals_dict_table[] = {
    { MP_ROM_QSTR(MP_QSTR_vid), MP_ROM_PTR(&machine_usbh_device_vid_obj) },
    { MP_ROM_QSTR(MP_QSTR_pid), MP_ROM_PTR(&machine_usbh_device_pid_obj) },
    { MP_ROM_QSTR(MP_QSTR_manufacturer), MP_ROM_PTR(&machine_usbh_device_manufacturer_obj) },
    { MP_ROM_QSTR(MP_QSTR_product), MP_ROM_PTR(&machine_usbh_device_product_obj) },
    { MP_ROM_QSTR(MP_QSTR_serial), MP_ROM_PTR(&machine_usbh_device_serial_obj) },
};
static MP_DEFINE_CONST_DICT(machine_usbh_device_locals_dict, machine_usbh_device_locals_dict_table);

// Define the USBDevice type
MP_DEFINE_CONST_OBJ_TYPE(
    machine_usbh_device_type,
    MP_QSTR_USBDevice,
    MP_TYPE_FLAG_NONE,
    print, machine_usbh_device_print,
    locals_dict, &machine_usbh_device_locals_dict
    );

/******************************************************************************/
// MicroPython bindings for USBH_CDC

// Print function for USBH_CDC
static void machine_usbh_cdc_print(const mp_print_t *print, mp_obj_t self_in, mp_print_kind_t kind) {
    machine_usbh_cdc_obj_t *self = MP_OBJ_TO_PTR(self_in);
    mp_printf(print, "<USBH_CDC addr=%d itf=%d connected=%s>",
        self->dev_addr, self->itf_num, self->connected ? "True" : "False");
}

// Check if CDC device is connected
static mp_obj_t machine_usbh_cdc_is_connected(mp_obj_t self_in) {
    machine_usbh_cdc_obj_t *self = MP_OBJ_TO_PTR(self_in);
    return mp_obj_new_bool(DEVICE_ACTIVE(self));
}
static MP_DEFINE_CONST_FUN_OBJ_1(machine_usbh_cdc_is_connected_obj, machine_usbh_cdc_is_connected);

// Check how many bytes are available for reading
static mp_obj_t machine_usbh_cdc_any(mp_obj_t self_in) {
    machine_usbh_cdc_obj_t *self = MP_OBJ_TO_PTR(self_in);

    if (!DEVICE_ACTIVE(self)) {
        return mp_obj_new_int(0);
    }

    return mp_obj_new_int(tuh_cdc_available(self->itf_num));
}
static MP_DEFINE_CONST_FUN_OBJ_1(machine_usbh_cdc_any_obj, machine_usbh_cdc_any);

// Read data from CDC device
static mp_obj_t machine_usbh_cdc_read(size_t n_args, const mp_obj_t *args) {
    machine_usbh_cdc_obj_t *self = MP_OBJ_TO_PTR(args[0]);

    if (!DEVICE_ACTIVE(self)) {
        mp_raise_OSError(MP_ENODEV);
    }

    mp_int_t nbytes = -1;
    if (n_args > 1) {
        nbytes = mp_obj_get_int(args[1]);
    }

    if (nbytes == 0) {
        return mp_obj_new_bytes(NULL, 0);
    }

    // Get available data size
    uint32_t available = tuh_cdc_available(self->itf_num);
    if (available == 0) {
        if (nbytes == -1) {
            return mp_obj_new_bytes(NULL, 0);
        }
        // Wait for data if specific number requested
        mp_hal_delay_ms(1);
        available = tuh_cdc_available(self->itf_num);
        if (available == 0) {
            return mp_obj_new_bytes(NULL, 0);
        }
    }

    // Determine how much to read
    uint32_t to_read = available;
    if (nbytes > 0 && (uint32_t)nbytes < to_read) {
        to_read = nbytes;
    }

    // Allocate buffer and read data
    vstr_t vstr;
    vstr_init_len(&vstr, to_read);
    uint32_t bytes_read = tuh_cdc_read(self->itf_num, vstr.buf, to_read);

    if (bytes_read < to_read) {
        vstr.len = bytes_read;
        vstr.buf[bytes_read] = '\0';
    }

    return mp_obj_new_str_from_vstr(&mp_type_bytes, &vstr);
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(machine_usbh_cdc_read_obj, 1, 2, machine_usbh_cdc_read);

// Write data to CDC device
static mp_obj_t machine_usbh_cdc_write(mp_obj_t self_in, mp_obj_t buf_in) {
    machine_usbh_cdc_obj_t *self = MP_OBJ_TO_PTR(self_in);

    if (!DEVICE_ACTIVE(self)) {
        mp_raise_OSError(MP_ENODEV);
    }

    mp_buffer_info_t bufinfo;
    mp_get_buffer_raise(buf_in, &bufinfo, MP_BUFFER_READ);

    uint32_t bytes_written = tuh_cdc_write(self->itf_num, bufinfo.buf, bufinfo.len);
    if (bytes_written > 0) {
        tuh_cdc_write_flush(self->itf_num);
        // Small delay to allow write to complete
        mp_hal_delay_ms(1);
    }

    return mp_obj_new_int(bytes_written);
}
static MP_DEFINE_CONST_FUN_OBJ_2(machine_usbh_cdc_write_obj, machine_usbh_cdc_write);

// Set IRQ callback for CDC device
static mp_obj_t machine_usbh_cdc_irq(size_t n_args, const mp_obj_t *pos_args, mp_map_t *kw_args) {
    enum { ARG_handler, ARG_trigger };
    static const mp_arg_t allowed_args[] = {
        { MP_QSTR_handler, MP_ARG_OBJ, {.u_obj = mp_const_none} },
        { MP_QSTR_trigger, MP_ARG_INT, {.u_int = USBH_CDC_IRQ_RX} },
    };

    machine_usbh_cdc_obj_t *self = MP_OBJ_TO_PTR(pos_args[0]);
    mp_arg_val_t args[MP_ARRAY_SIZE(allowed_args)];
    mp_arg_parse_all(n_args - 1, pos_args + 1, kw_args, MP_ARRAY_SIZE(allowed_args), allowed_args, args);

    if (args[ARG_handler].u_obj == mp_const_none) {
        // Disable callback
        self->irq_callback = mp_const_none;
    } else {
        // Set callback
        self->irq_callback = args[ARG_handler].u_obj;
    }

    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_KW(machine_usbh_cdc_irq_obj, 1, machine_usbh_cdc_irq);

// Stream methods for CDC
static mp_uint_t machine_usbh_cdc_read_method(mp_obj_t self_in, void *buf_in, mp_uint_t size, int *errcode) {
    machine_usbh_cdc_obj_t *self = MP_OBJ_TO_PTR(self_in);

    if (!DEVICE_ACTIVE(self)) {
        *errcode = MP_ENODEV;
        return MP_STREAM_ERROR;
    }

    uint32_t bytes_read = tuh_cdc_read(self->itf_num, buf_in, size);
    return bytes_read;
}

static mp_uint_t machine_usbh_cdc_write_method(mp_obj_t self_in, const void *buf_in, mp_uint_t size, int *errcode) {
    machine_usbh_cdc_obj_t *self = MP_OBJ_TO_PTR(self_in);

    if (!DEVICE_ACTIVE(self)) {
        *errcode = MP_ENODEV;
        return MP_STREAM_ERROR;
    }

    uint32_t bytes_written = tuh_cdc_write(self->itf_num, buf_in, size);
    if (bytes_written > 0) {
        tuh_cdc_write_flush(self->itf_num);
        // Small delay to allow write to complete
        mp_hal_delay_ms(1);
    }
    return bytes_written;
}

static mp_uint_t machine_usbh_cdc_ioctl_method(mp_obj_t self_in, mp_uint_t request, uintptr_t arg, int *errcode) {
    machine_usbh_cdc_obj_t *self = MP_OBJ_TO_PTR(self_in);

    switch (request) {
        case MP_STREAM_POLL: {
            uintptr_t ret = 0;
            if (DEVICE_ACTIVE(self) && tuh_cdc_available(self->itf_num) > 0) {
                ret |= MP_STREAM_POLL_RD;
            }
            if (DEVICE_ACTIVE(self)) {
                ret |= MP_STREAM_POLL_WR;
            }
            return ret;
        }
        case MP_STREAM_CLOSE:
            return 0;
        default:
            *errcode = MP_EINVAL;
            return MP_STREAM_ERROR;
    }
}

// USBH_CDC constants
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

// Define the USBH_CDC type
MP_DEFINE_CONST_OBJ_TYPE(
    machine_usbh_cdc_type,
    MP_QSTR_USBH_CDC,
    MP_TYPE_FLAG_NONE,
    print, machine_usbh_cdc_print,
    protocol, &machine_usbh_cdc_stream_p,
    locals_dict, &machine_usbh_cdc_locals_dict
    );

/******************************************************************************/
// MicroPython bindings for USBH_MSC

// Print function for USBH_MSC
static void machine_usbh_msc_print(const mp_print_t *print, mp_obj_t self_in, mp_print_kind_t kind) {
    machine_usbh_msc_obj_t *self = MP_OBJ_TO_PTR(self_in);
    mp_printf(print, "<USBH_MSC addr=%d lun=%d blocks=%lu size=%lu connected=%s>",
        self->dev_addr, self->lun, self->block_count, self->block_size,
        self->connected ? "True" : "False");
}

// Check if MSC device is connected
static mp_obj_t machine_usbh_msc_is_connected(mp_obj_t self_in) {
    machine_usbh_msc_obj_t *self = MP_OBJ_TO_PTR(self_in);
    return mp_obj_new_bool(DEVICE_ACTIVE(self));
}
static MP_DEFINE_CONST_FUN_OBJ_1(machine_usbh_msc_is_connected_obj, machine_usbh_msc_is_connected);

// Read blocks from MSC device
static mp_obj_t machine_usbh_msc_readblocks(size_t n_args, const mp_obj_t *args) {
    machine_usbh_msc_obj_t *self = MP_OBJ_TO_PTR(args[0]);

    if (!DEVICE_ACTIVE(self)) {
        mp_raise_OSError(MP_ENODEV);
    }

    mp_uint_t block_num = mp_obj_get_int(args[1]);
    mp_buffer_info_t bufinfo;
    mp_get_buffer_raise(args[2], &bufinfo, MP_BUFFER_WRITE);
    mp_uint_t offset = 0;
    if (n_args > 3) {
        offset = mp_obj_get_int(args[3]);
    }

    if (bufinfo.len < self->block_size) {
        mp_raise_ValueError(MP_ERROR_TEXT("buffer too small"));
    }

    bool success = tuh_msc_read10(self->dev_addr, self->lun,
        (uint8_t *)bufinfo.buf + offset,
        block_num, 1);

    if (!success) {
        mp_raise_OSError(MP_EIO);
    }

    // Wait for completion (simplified)
    mp_hal_delay_ms(10);

    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(machine_usbh_msc_readblocks_obj, 3, 4, machine_usbh_msc_readblocks);

// Write blocks to MSC device
static mp_obj_t machine_usbh_msc_writeblocks(size_t n_args, const mp_obj_t *args) {
    machine_usbh_msc_obj_t *self = MP_OBJ_TO_PTR(args[0]);

    if (!DEVICE_ACTIVE(self)) {
        mp_raise_OSError(MP_ENODEV);
    }

    if (self->readonly) {
        mp_raise_OSError(MP_EROFS);
    }

    mp_uint_t block_num = mp_obj_get_int(args[1]);
    mp_buffer_info_t bufinfo;
    mp_get_buffer_raise(args[2], &bufinfo, MP_BUFFER_READ);
    mp_uint_t offset = 0;
    if (n_args > 3) {
        offset = mp_obj_get_int(args[3]);
    }

    if (bufinfo.len < self->block_size) {
        mp_raise_ValueError(MP_ERROR_TEXT("buffer too small"));
    }

    bool success = tuh_msc_write10(self->dev_addr, self->lun,
        (uint8_t *)bufinfo.buf + offset,
        block_num, 1);

    if (!success) {
        mp_raise_OSError(MP_EIO);
    }

    // Wait for completion (simplified)
    mp_hal_delay_ms(10);

    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(machine_usbh_msc_writeblocks_obj, 3, 4, machine_usbh_msc_writeblocks);

// Control MSC device
static mp_obj_t machine_usbh_msc_ioctl(mp_obj_t self_in, mp_obj_t op_in, mp_obj_t arg_in) {
    machine_usbh_msc_obj_t *self = MP_OBJ_TO_PTR(self_in);
    mp_uint_t op = mp_obj_get_int(op_in);

    switch (op) {
        case MP_BLOCKDEV_IOCTL_INIT:
            return mp_obj_new_bool(DEVICE_ACTIVE(self));

        case MP_BLOCKDEV_IOCTL_DEINIT:
            return mp_const_none;

        case MP_BLOCKDEV_IOCTL_SYNC:
            return mp_const_none;

        case MP_BLOCKDEV_IOCTL_BLOCK_COUNT:
            return mp_obj_new_int(self->block_count);

        case MP_BLOCKDEV_IOCTL_BLOCK_SIZE:
            return mp_obj_new_int(self->block_size);

        case MP_BLOCKDEV_IOCTL_BLOCK_ERASE:
            // MSC doesn't typically support erase
            return mp_const_none;

        default:
            return mp_const_none;
    }
}
static MP_DEFINE_CONST_FUN_OBJ_3(machine_usbh_msc_ioctl_obj, machine_usbh_msc_ioctl);

// USBH_MSC class methods table
static const mp_rom_map_elem_t machine_usbh_msc_locals_dict_table[] = {
    { MP_ROM_QSTR(MP_QSTR_is_connected), MP_ROM_PTR(&machine_usbh_msc_is_connected_obj) },
    { MP_ROM_QSTR(MP_QSTR_readblocks), MP_ROM_PTR(&machine_usbh_msc_readblocks_obj) },
    { MP_ROM_QSTR(MP_QSTR_writeblocks), MP_ROM_PTR(&machine_usbh_msc_writeblocks_obj) },
    { MP_ROM_QSTR(MP_QSTR_ioctl), MP_ROM_PTR(&machine_usbh_msc_ioctl_obj) },
};
static MP_DEFINE_CONST_DICT(machine_usbh_msc_locals_dict, machine_usbh_msc_locals_dict_table);

// Define the USBH_MSC type
MP_DEFINE_CONST_OBJ_TYPE(
    machine_usbh_msc_type,
    MP_QSTR_USBH_MSC,
    MP_TYPE_FLAG_NONE,
    print, machine_usbh_msc_print,
    locals_dict, &machine_usbh_msc_locals_dict
    );

/******************************************************************************/
// MicroPython bindings for USBH_HID

// Print function for USBH_HID
static void machine_usbh_hid_print(const mp_print_t *print, mp_obj_t self_in, mp_print_kind_t kind) {
    machine_usbh_hid_obj_t *self = MP_OBJ_TO_PTR(self_in);
    mp_printf(print, "<USBH_HID addr=%d inst=%d protocol=%d connected=%s>",
        self->dev_addr, self->instance, self->protocol,
        self->connected ? "True" : "False");
}

// Check if HID device is connected
static mp_obj_t machine_usbh_hid_is_connected(mp_obj_t self_in) {
    machine_usbh_hid_obj_t *self = MP_OBJ_TO_PTR(self_in);
    return mp_obj_new_bool(DEVICE_ACTIVE(self));
}
static MP_DEFINE_CONST_FUN_OBJ_1(machine_usbh_hid_is_connected_obj, machine_usbh_hid_is_connected);

// Get latest report from HID device
static mp_obj_t machine_usbh_hid_get_report(mp_obj_t self_in) {
    machine_usbh_hid_obj_t *self = MP_OBJ_TO_PTR(self_in);

    if (!DEVICE_ACTIVE(self)) {
        return mp_const_none;
    }

    return self->latest_report;
}
static MP_DEFINE_CONST_FUN_OBJ_1(machine_usbh_hid_get_report_obj, machine_usbh_hid_get_report);

// Set IRQ callback for HID device
static mp_obj_t machine_usbh_hid_irq(size_t n_args, const mp_obj_t *pos_args, mp_map_t *kw_args) {
    enum { ARG_handler, ARG_trigger };
    static const mp_arg_t allowed_args[] = {
        { MP_QSTR_handler, MP_ARG_OBJ, {.u_obj = mp_const_none} },
        { MP_QSTR_trigger, MP_ARG_INT, {.u_int = USBH_HID_IRQ_REPORT} },
    };

    machine_usbh_hid_obj_t *self = MP_OBJ_TO_PTR(pos_args[0]);
    mp_arg_val_t args[MP_ARRAY_SIZE(allowed_args)];
    mp_arg_parse_all(n_args - 1, pos_args + 1, kw_args, MP_ARRAY_SIZE(allowed_args), allowed_args, args);

    if (args[ARG_handler].u_obj == mp_const_none) {
        // Disable callback
        self->irq_callback = mp_const_none;
    } else {
        // Set callback
        self->irq_callback = args[ARG_handler].u_obj;
    }

    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_KW(machine_usbh_hid_irq_obj, 1, machine_usbh_hid_irq);

// USBH_HID class methods table
static const mp_rom_map_elem_t machine_usbh_hid_locals_dict_table[] = {
    { MP_ROM_QSTR(MP_QSTR_is_connected), MP_ROM_PTR(&machine_usbh_hid_is_connected_obj) },
    { MP_ROM_QSTR(MP_QSTR_get_report), MP_ROM_PTR(&machine_usbh_hid_get_report_obj) },
    { MP_ROM_QSTR(MP_QSTR_irq), MP_ROM_PTR(&machine_usbh_hid_irq_obj) },

    // Constants
    { MP_ROM_QSTR(MP_QSTR_IRQ_REPORT), MP_ROM_INT(USBH_HID_IRQ_REPORT) },
    { MP_ROM_QSTR(MP_QSTR_PROTOCOL_KEYBOARD), MP_ROM_INT(USBH_HID_PROTOCOL_KEYBOARD) },
    { MP_ROM_QSTR(MP_QSTR_PROTOCOL_MOUSE), MP_ROM_INT(USBH_HID_PROTOCOL_MOUSE) },
    { MP_ROM_QSTR(MP_QSTR_PROTOCOL_GENERIC), MP_ROM_INT(USBH_HID_PROTOCOL_GENERIC) },
};
static MP_DEFINE_CONST_DICT(machine_usbh_hid_locals_dict, machine_usbh_hid_locals_dict_table);

// Define the USBH_HID type
MP_DEFINE_CONST_OBJ_TYPE(
    machine_usbh_hid_type,
    MP_QSTR_USBH_HID,
    MP_TYPE_FLAG_NONE,
    print, machine_usbh_hid_print,
    locals_dict, &machine_usbh_hid_locals_dict
    );

#endif // MICROPY_HW_USB_HOST
