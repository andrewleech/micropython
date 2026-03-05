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

#include <string.h>

#include "py/obj.h"
#include "py/runtime.h"
#include "py/objstr.h"
#include "py/mperrno.h"
#include "py/stream.h"
#include "py/mphal.h"
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
    int count = 0;
    for (int i = 0; i < CFG_TUH_DEVICE_MAX; i++) {
        if (self->device_pool[i].mounted) {
            count++;
        }
    }
    mp_printf(print, "<USBHost devices: %d>", count);
}

// Create a new USBHost object
static mp_obj_t machine_usb_host_make_new(const mp_obj_type_t *type, size_t n_args, size_t n_kw, const mp_obj_t *args) {
    // Parse arguments (none for now)
    mp_arg_check_num(n_args, n_kw, 0, 0, false);

    // Create the self object
    if (MP_STATE_VM(usbh) == MP_OBJ_NULL) {
        mp_obj_usb_host_t *self = mp_obj_malloc(mp_obj_usb_host_t, type);
        self->initialized = false;
        self->active = false;

        // Initialize device pools.
        for (int i = 0; i < CFG_TUH_DEVICE_MAX; i++) {
            self->device_pool[i].base.type = &machine_usbh_device_type;
            self->device_pool[i].mounted = false;
        }
        for (int i = 0; i < CFG_TUH_CDC; i++) {
            self->cdc_pool[i].base.type = &machine_usbh_cdc_type;
            self->cdc_pool[i].connected = false;
            self->cdc_pool[i].irq_callback = mp_const_none;
        }
        for (int i = 0; i < CFG_TUH_MSC; i++) {
            self->msc_pool[i].base.type = &machine_usbh_msc_type;
            self->msc_pool[i].connected = false;
        }
        for (int i = 0; i < CFG_TUH_HID; i++) {
            self->hid_pool[i].base.type = &machine_usbh_hid_type;
            self->hid_pool[i].connected = false;
            self->hid_pool[i].irq_callback = mp_const_none;
        }
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

        if (new_active && MP_STATE_VM(usbh) == MP_OBJ_NULL) {
            mp_raise_msg(&mp_type_RuntimeError, MP_ERROR_TEXT("USBHost has been deinited, create new object"));
        }

        if (new_active) {
            if (!self->initialized) {
                // First activation - full initialization
                mp_usbh_init_tuh();
                self->initialized = true;
            } else if (!self->active) {
                // Re-activating after deactivation - just re-enable interrupts
                mp_usbh_int_enable();
            }
        } else if (self->active) {
            // Deactivating - disable interrupts and clear state.
            mp_usbh_int_disable();

            // Clear device pools to prevent stale references.
            for (int i = 0; i < CFG_TUH_DEVICE_MAX; i++) {
                self->device_pool[i].mounted = false;
            }
            for (int i = 0; i < CFG_TUH_CDC; i++) {
                self->cdc_pool[i].connected = false;
            }
            for (int i = 0; i < CFG_TUH_MSC; i++) {
                self->msc_pool[i].connected = false;
            }
            for (int i = 0; i < CFG_TUH_HID; i++) {
                self->hid_pool[i].connected = false;
            }
        }

        self->active = new_active;
        return mp_const_none;
    }
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(machine_usb_host_active_obj, 1, 2, machine_usb_host_active);

// Method to deinitialize USB Host
static mp_obj_t machine_usb_host_deinit(mp_obj_t self_in) {
    mp_usbh_deinit();
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(machine_usb_host_deinit_obj, machine_usb_host_deinit);

// Method to get the tuple of devices.
static mp_obj_t machine_usb_host_devices(mp_obj_t self_in) {
    mp_obj_usb_host_t *self = MP_OBJ_TO_PTR(self_in);
    mp_obj_t items[CFG_TUH_DEVICE_MAX];
    size_t count = 0;
    for (int i = 0; i < CFG_TUH_DEVICE_MAX; i++) {
        if (self->device_pool[i].mounted) {
            items[count++] = MP_OBJ_FROM_PTR(&self->device_pool[i]);
        }
    }
    return mp_obj_new_tuple(count, items);
}
static MP_DEFINE_CONST_FUN_OBJ_1(machine_usb_host_devices_obj, machine_usb_host_devices);

// Method to get CDC devices.
static mp_obj_t machine_usb_host_cdc_devices(mp_obj_t self_in) {
    mp_obj_usb_host_t *self = MP_OBJ_TO_PTR(self_in);
    mp_obj_t items[CFG_TUH_CDC];
    size_t count = 0;
    for (int i = 0; i < CFG_TUH_CDC; i++) {
        if (self->cdc_pool[i].connected) {
            items[count++] = MP_OBJ_FROM_PTR(&self->cdc_pool[i]);
        }
    }
    return mp_obj_new_tuple(count, items);
}
static MP_DEFINE_CONST_FUN_OBJ_1(machine_usb_host_cdc_devices_obj, machine_usb_host_cdc_devices);

// Method to get MSC devices.
static mp_obj_t machine_usb_host_msc_devices(mp_obj_t self_in) {
    mp_obj_usb_host_t *self = MP_OBJ_TO_PTR(self_in);
    mp_obj_t items[CFG_TUH_MSC];
    size_t count = 0;
    for (int i = 0; i < CFG_TUH_MSC; i++) {
        if (self->msc_pool[i].connected) {
            items[count++] = MP_OBJ_FROM_PTR(&self->msc_pool[i]);
        }
    }
    return mp_obj_new_tuple(count, items);
}
static MP_DEFINE_CONST_FUN_OBJ_1(machine_usb_host_msc_devices_obj, machine_usb_host_msc_devices);

// Method to get HID devices.
static mp_obj_t machine_usb_host_hid_devices(mp_obj_t self_in) {
    mp_obj_usb_host_t *self = MP_OBJ_TO_PTR(self_in);
    mp_obj_t items[CFG_TUH_HID];
    size_t count = 0;
    for (int i = 0; i < CFG_TUH_HID; i++) {
        if (self->hid_pool[i].connected) {
            items[count++] = MP_OBJ_FROM_PTR(&self->hid_pool[i]);
        }
    }
    return mp_obj_new_tuple(count, items);
}
static MP_DEFINE_CONST_FUN_OBJ_1(machine_usb_host_hid_devices_obj, machine_usb_host_hid_devices);

// USBHost class methods table
static const mp_rom_map_elem_t machine_usb_host_locals_dict_table[] = {
    { MP_ROM_QSTR(MP_QSTR_active), MP_ROM_PTR(&machine_usb_host_active_obj) },
    { MP_ROM_QSTR(MP_QSTR_deinit), MP_ROM_PTR(&machine_usb_host_deinit_obj) },
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
// MicroPython bindings for USBH_Device

// Print function for USBH_Device
static void machine_usbh_device_print(const mp_print_t *print, mp_obj_t self_in, mp_print_kind_t kind) {
    machine_usbh_device_obj_t *self = MP_OBJ_TO_PTR(self_in);
    mp_printf(print, "<USBH_Device addr=%d VID=%04x PID=%04x>", self->addr, self->vid, self->pid);
}

// Attribute access for USBH_Device — read-only properties with lazy descriptor fetch.
static void machine_usbh_device_attr(mp_obj_t self_in, qstr attr, mp_obj_t *dest) {
    machine_usbh_device_obj_t *self = MP_OBJ_TO_PTR(self_in);
    if (dest[0] != MP_OBJ_NULL) {
        return; // Read-only, reject stores.
    }
    if (attr == MP_QSTR_vid) {
        dest[0] = mp_obj_new_int(self->vid);
    } else if (attr == MP_QSTR_pid) {
        dest[0] = mp_obj_new_int(self->pid);
    } else if (attr == MP_QSTR_manufacturer) {
        mp_usbh_fetch_device_strings(self);
        dest[0] = self->manufacturer[0] ? mp_obj_new_str(self->manufacturer, strlen(self->manufacturer)) : mp_const_none;
    } else if (attr == MP_QSTR_product) {
        mp_usbh_fetch_device_strings(self);
        dest[0] = self->product[0] ? mp_obj_new_str(self->product, strlen(self->product)) : mp_const_none;
    } else if (attr == MP_QSTR_serial) {
        mp_usbh_fetch_device_strings(self);
        dest[0] = self->serial[0] ? mp_obj_new_str(self->serial, strlen(self->serial)) : mp_const_none;
    } else if (attr == MP_QSTR_dev_class) {
        mp_usbh_fetch_device_strings(self);
        dest[0] = mp_obj_new_int(self->dev_class);
    } else if (attr == MP_QSTR_dev_subclass) {
        mp_usbh_fetch_device_strings(self);
        dest[0] = mp_obj_new_int(self->dev_subclass);
    } else if (attr == MP_QSTR_dev_protocol) {
        mp_usbh_fetch_device_strings(self);
        dest[0] = mp_obj_new_int(self->dev_protocol);
    } else {
        // Delegate to type hierarchy for method lookup.
        dest[1] = MP_OBJ_SENTINEL;
    }
}

// Define the USBH_Device type
MP_DEFINE_CONST_OBJ_TYPE(
    machine_usbh_device_type,
    MP_QSTR_USBH_Device,
    MP_TYPE_FLAG_NONE,
    print, machine_usbh_device_print,
    attr, machine_usbh_device_attr
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

    return mp_obj_new_int(tuh_cdc_read_available(self->itf_num));
}
static MP_DEFINE_CONST_FUN_OBJ_1(machine_usbh_cdc_any_obj, machine_usbh_cdc_any);

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

    // Currently only IRQ_RX is supported; trigger arg is accepted for forward compat.
    (void)args[ARG_trigger].u_int;

    if (args[ARG_handler].u_obj == mp_const_none) {
        self->irq_callback = mp_const_none;
    } else {
        self->irq_callback = args[ARG_handler].u_obj;
    }

    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_KW(machine_usbh_cdc_irq_obj, 1, machine_usbh_cdc_irq);

// Set CDC line coding parameters.
static mp_obj_t machine_usbh_cdc_set_line_coding(size_t n_args, const mp_obj_t *pos_args, mp_map_t *kw_args) {
    enum { ARG_baudrate, ARG_bits, ARG_parity, ARG_stop };
    static const mp_arg_t allowed_args[] = {
        { MP_QSTR_baudrate, MP_ARG_INT, {.u_int = 115200} },
        { MP_QSTR_bits, MP_ARG_INT, {.u_int = 8} },
        { MP_QSTR_parity, MP_ARG_INT, {.u_int = 0} },
        { MP_QSTR_stop, MP_ARG_INT, {.u_int = 0} },
    };

    machine_usbh_cdc_obj_t *self = MP_OBJ_TO_PTR(pos_args[0]);
    mp_arg_val_t args[MP_ARRAY_SIZE(allowed_args)];
    mp_arg_parse_all(n_args - 1, pos_args + 1, kw_args, MP_ARRAY_SIZE(allowed_args), allowed_args, args);

    if (!DEVICE_ACTIVE(self)) {
        mp_raise_OSError(MP_ENODEV);
    }

    cdc_line_coding_t line_coding = {
        .bit_rate = (uint32_t)args[ARG_baudrate].u_int,
        .stop_bits = (uint8_t)args[ARG_stop].u_int,
        .parity = (uint8_t)args[ARG_parity].u_int,
        .data_bits = (uint8_t)args[ARG_bits].u_int,
    };
    tuh_cdc_set_line_coding(self->itf_num, &line_coding, NULL, 0);

    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_KW(machine_usbh_cdc_set_line_coding_obj, 1, machine_usbh_cdc_set_line_coding);

// Stream read method for CDC — delegates to TinyUSB FIFO.
static mp_uint_t machine_usbh_cdc_read_method(mp_obj_t self_in, void *buf_in, mp_uint_t size, int *errcode) {
    machine_usbh_cdc_obj_t *self = MP_OBJ_TO_PTR(self_in);

    if (!DEVICE_ACTIVE(self)) {
        *errcode = MP_ENODEV;
        return MP_STREAM_ERROR;
    }

    uint32_t bytes_read = tuh_cdc_read(self->itf_num, buf_in, size);
    if (bytes_read == 0) {
        *errcode = MP_EAGAIN;
        return MP_STREAM_ERROR;
    }
    return bytes_read;
}

// Stream write method for CDC with retry and timeout.
// Adapted from mp_usbd_cdc_tx_strn() in shared/tinyusb/mp_usbd_cdc.c.
static mp_uint_t machine_usbh_cdc_write_method(mp_obj_t self_in, const void *buf_in, mp_uint_t size, int *errcode) {
    machine_usbh_cdc_obj_t *self = MP_OBJ_TO_PTR(self_in);

    if (!DEVICE_ACTIVE(self)) {
        *errcode = MP_ENODEV;
        return MP_STREAM_ERROR;
    }

    const uint8_t *buf = buf_in;
    mp_uint_t last_write = mp_hal_ticks_ms();
    size_t i = 0;
    while (i < size) {
        uint32_t n = MIN(size - i, tuh_cdc_write_available(self->itf_num));
        uint32_t n2 = tuh_cdc_write(self->itf_num, buf + i, n);
        if (n2 > 0) {
            tuh_cdc_write_flush(self->itf_num);
            i += n2;
            last_write = mp_hal_ticks_ms();
        } else {
            if (mp_hal_ticks_ms() - last_write >= 1000) {
                break; // Timeout after 1 second of no progress.
            }
            mp_event_wait_ms(1);
            // Explicitly process USB events — mp_event_wait_ms() alone may
            // not trigger the scheduled mp_usbh_task on all ports.
            tuh_task_ext(0, false);
        }
        if (!DEVICE_ACTIVE(self)) {
            break;
        }
    }
    if (i == 0) {
        *errcode = MP_EAGAIN;
        return MP_STREAM_ERROR;
    }
    return i;
}

static mp_uint_t machine_usbh_cdc_ioctl_method(mp_obj_t self_in, mp_uint_t request, uintptr_t arg, int *errcode) {
    machine_usbh_cdc_obj_t *self = MP_OBJ_TO_PTR(self_in);

    switch (request) {
        case MP_STREAM_POLL: {
            mp_uint_t flags = arg;
            uintptr_t ret = 0;
            if ((flags & MP_STREAM_POLL_RD) && DEVICE_ACTIVE(self) && tuh_cdc_read_available(self->itf_num) > 0) {
                ret |= MP_STREAM_POLL_RD;
            }
            if ((flags & MP_STREAM_POLL_WR) && DEVICE_ACTIVE(self) && tuh_cdc_write_available(self->itf_num) > 0) {
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

// USBH_CDC locals dict
static const mp_rom_map_elem_t machine_usbh_cdc_locals_dict_table[] = {
    { MP_ROM_QSTR(MP_QSTR_is_connected), MP_ROM_PTR(&machine_usbh_cdc_is_connected_obj) },
    { MP_ROM_QSTR(MP_QSTR_any), MP_ROM_PTR(&machine_usbh_cdc_any_obj) },
    { MP_ROM_QSTR(MP_QSTR_irq), MP_ROM_PTR(&machine_usbh_cdc_irq_obj) },
    { MP_ROM_QSTR(MP_QSTR_set_line_coding), MP_ROM_PTR(&machine_usbh_cdc_set_line_coding_obj) },

    // Standard stream methods — delegate to stream protocol.
    { MP_ROM_QSTR(MP_QSTR_read), MP_ROM_PTR(&mp_stream_read1_obj) },
    { MP_ROM_QSTR(MP_QSTR_readline), MP_ROM_PTR(&mp_stream_unbuffered_readline_obj) },
    { MP_ROM_QSTR(MP_QSTR_readinto), MP_ROM_PTR(&mp_stream_readinto_obj) },
    { MP_ROM_QSTR(MP_QSTR_write), MP_ROM_PTR(&mp_stream_write1_obj) },
    { MP_ROM_QSTR(MP_QSTR_close), MP_ROM_PTR(&mp_stream_close_obj) },

    // Constants
    { MP_ROM_QSTR(MP_QSTR_IRQ_RX), MP_ROM_INT(USBH_CDC_IRQ_RX) },
};
static MP_DEFINE_CONST_DICT(machine_usbh_cdc_locals_dict, machine_usbh_cdc_locals_dict_table);

// Define the USBH_CDC type
MP_DEFINE_CONST_OBJ_TYPE(
    machine_usbh_cdc_type,
    MP_QSTR_USBH_CDC,
    MP_TYPE_FLAG_ITER_IS_STREAM,
    print, machine_usbh_cdc_print,
    protocol, &machine_usbh_cdc_stream_p,
    locals_dict, &machine_usbh_cdc_locals_dict
    );

/******************************************************************************/
// MicroPython bindings for USBH_MSC

// Print function for USBH_MSC
static void machine_usbh_msc_print(const mp_print_t *print, mp_obj_t self_in, mp_print_kind_t kind) {
    machine_usbh_msc_obj_t *self = MP_OBJ_TO_PTR(self_in);
    mp_printf(print, "<USBH_MSC addr=%u lun=%u blocks=%u size=%u connected=%s>",
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

    if (block_num >= self->block_count) {
        mp_raise_ValueError(MP_ERROR_TEXT("block out of range"));
    }

    // With offset: read one block at byte offset within buffer
    // Without offset: read len(buf)/block_size blocks
    uint16_t num_blocks;
    if (n_args > 3) {
        if (offset + self->block_size > bufinfo.len) {
            mp_raise_ValueError(MP_ERROR_TEXT("buffer too small"));
        }
        num_blocks = 1;
    } else {
        if (bufinfo.len < self->block_size) {
            mp_raise_ValueError(MP_ERROR_TEXT("buffer too small"));
        }
        num_blocks = bufinfo.len / self->block_size;
        if (block_num + num_blocks > self->block_count) {
            num_blocks = self->block_count - block_num;
        }
    }

    // Validate DMA buffer alignment (CFG_TUH_MEM_ALIGN requires 4-byte).
    uint8_t *transfer_buf = (uint8_t *)bufinfo.buf + offset;
    if ((uintptr_t)transfer_buf & 3) {
        mp_raise_ValueError(MP_ERROR_TEXT("buffer must be 4-byte aligned"));
    }

    // Start async operation
    self->operation_pending = true;
    self->operation_success = false;

    bool started = tuh_msc_read10(
        self->dev_addr,
        self->lun,
        transfer_buf,
        block_num,
        num_blocks,
        mp_usbh_msc_xfer_complete,
        0
        );

    if (!started) {
        self->operation_pending = false;
        mp_raise_OSError(MP_EIO);
    }

    if (!mp_usbh_msc_wait_complete(self, MICROPY_HW_USBH_MSC_TIMEOUT)) {
        self->operation_pending = false;
        mp_raise_OSError(MP_ETIMEDOUT);
    }

    if (!self->operation_success) {
        mp_raise_OSError(MP_EIO);
    }

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

    if (block_num >= self->block_count) {
        mp_raise_ValueError(MP_ERROR_TEXT("block out of range"));
    }

    // With offset: write one block at byte offset within buffer
    // Without offset: write len(buf)/block_size blocks
    uint16_t num_blocks;
    if (n_args > 3) {
        if (offset + self->block_size > bufinfo.len) {
            mp_raise_ValueError(MP_ERROR_TEXT("buffer too small"));
        }
        num_blocks = 1;
    } else {
        if (bufinfo.len < self->block_size) {
            mp_raise_ValueError(MP_ERROR_TEXT("buffer too small"));
        }
        num_blocks = bufinfo.len / self->block_size;
        if (block_num + num_blocks > self->block_count) {
            num_blocks = self->block_count - block_num;
        }
    }

    // Validate DMA buffer alignment (CFG_TUH_MEM_ALIGN requires 4-byte).
    uint8_t *transfer_buf = (uint8_t *)bufinfo.buf + offset;
    if ((uintptr_t)transfer_buf & 3) {
        mp_raise_ValueError(MP_ERROR_TEXT("buffer must be 4-byte aligned"));
    }

    // Start async operation
    self->operation_pending = true;
    self->operation_success = false;

    bool started = tuh_msc_write10(
        self->dev_addr,
        self->lun,
        transfer_buf,
        block_num,
        num_blocks,
        mp_usbh_msc_xfer_complete,
        0
        );

    if (!started) {
        self->operation_pending = false;
        mp_raise_OSError(MP_EIO);
    }

    if (!mp_usbh_msc_wait_complete(self, MICROPY_HW_USBH_MSC_TIMEOUT)) {
        self->operation_pending = false;
        mp_raise_OSError(MP_ETIMEDOUT);
    }

    if (!self->operation_success) {
        mp_raise_OSError(MP_EIO);
    }

    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(machine_usbh_msc_writeblocks_obj, 3, 4, machine_usbh_msc_writeblocks);

// Control MSC device
static mp_obj_t machine_usbh_msc_ioctl(mp_obj_t self_in, mp_obj_t op_in, mp_obj_t arg_in) {
    machine_usbh_msc_obj_t *self = MP_OBJ_TO_PTR(self_in);
    mp_uint_t op = mp_obj_get_int(op_in);

    switch (op) {
        case MP_BLOCKDEV_IOCTL_INIT:
            // Return 0 on success, non-zero on failure (diskio convention)
            return DEVICE_ACTIVE(self) ? MP_OBJ_NEW_SMALL_INT(0) : MP_OBJ_NEW_SMALL_INT(-1);

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

    if (!DEVICE_ACTIVE(self) || !self->report_ready) {
        return mp_const_none;
    }

    // Create bytes object in safe context (not in interrupt/callback)
    mp_obj_t report = mp_obj_new_bytes(self->report_buffer, self->report_len);
    self->report_ready = false; // Clear flag after reading

    return report;
}
static MP_DEFINE_CONST_FUN_OBJ_1(machine_usbh_hid_get_report_obj, machine_usbh_hid_get_report);

// Read report into user-provided buffer (zero-allocation variant).
static mp_obj_t machine_usbh_hid_readinto(mp_obj_t self_in, mp_obj_t buf_in) {
    machine_usbh_hid_obj_t *self = MP_OBJ_TO_PTR(self_in);
    mp_buffer_info_t bufinfo;
    mp_get_buffer_raise(buf_in, &bufinfo, MP_BUFFER_WRITE);

    if (!DEVICE_ACTIVE(self) || !self->report_ready) {
        return mp_obj_new_int(0);
    }

    size_t n = MIN(bufinfo.len, self->report_len);
    memcpy(bufinfo.buf, self->report_buffer, n);
    self->report_ready = false;
    return mp_obj_new_int(n);
}
static MP_DEFINE_CONST_FUN_OBJ_2(machine_usbh_hid_readinto_obj, machine_usbh_hid_readinto);

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

    // Currently only IRQ_REPORT is supported; trigger arg is accepted for forward compat.
    (void)args[ARG_trigger].u_int;

    if (args[ARG_handler].u_obj == mp_const_none) {
        self->irq_callback = mp_const_none;
    } else {
        self->irq_callback = args[ARG_handler].u_obj;
    }

    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_KW(machine_usbh_hid_irq_obj, 1, machine_usbh_hid_irq);

// USBH_HID class methods table
static const mp_rom_map_elem_t machine_usbh_hid_locals_dict_table[] = {
    { MP_ROM_QSTR(MP_QSTR_is_connected), MP_ROM_PTR(&machine_usbh_hid_is_connected_obj) },
    { MP_ROM_QSTR(MP_QSTR_get_report), MP_ROM_PTR(&machine_usbh_hid_get_report_obj) },
    { MP_ROM_QSTR(MP_QSTR_readinto), MP_ROM_PTR(&machine_usbh_hid_readinto_obj) },
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
