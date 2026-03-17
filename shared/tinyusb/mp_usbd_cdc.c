/*
 * This file is part of the MicroPython project, http://micropython.org/
 *
 * The MIT License (MIT)
 *
 * Copyright (c) 2022 Ibrahim Abdelkader <iabdalkader@openmv.io>
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

#include "py/runtime.h"
#include "py/mphal.h"
#include "py/stream.h"
#include "extmod/modmachine.h"

#include "mp_usbd.h"
#include "mp_usbd_cdc.h"

#if MICROPY_HW_USB_CDC && MICROPY_HW_ENABLE_USBDEV && !MICROPY_EXCLUDE_SHARED_TINYUSB_USBD_CDC

#if !MICROPY_HW_USB_CDC_STREAM
static uint8_t cdc_itf_pending; // keep track of cdc interfaces which need attention to poll
#endif
static int8_t cdc_connected_flush_delay = 0;

uintptr_t mp_usbd_cdc_poll_interfaces(uintptr_t poll_flags) {
    uintptr_t ret = 0;
    #if MICROPY_HW_USB_CDC_STREAM
    mp_usbd_task();
    #if MICROPY_KBD_EXCEPTION
    {
        // Lazily sync the TinyUSB wanted_char with mp_interrupt_char so that
        // Ctrl-C detection works without coupling interrupt_char.c to TinyUSB.
        static int last_interrupt_char = -1;
        if (mp_interrupt_char != last_interrupt_char) {
            last_interrupt_char = mp_interrupt_char;
            tud_cdc_set_wanted_char(mp_interrupt_char);
        }
    }
    #endif
    if ((poll_flags & MP_STREAM_POLL_RD) && tud_cdc_available()) {
        ret |= MP_STREAM_POLL_RD;
    }
    #else
    if (!cdc_itf_pending) {
        // Explicitly run the USB stack as the scheduler may be locked (eg we are in
        // an interrupt handler) while there is data pending.
        mp_usbd_task();
    }

    // any CDC interfaces left to poll?
    if (cdc_itf_pending && ringbuf_free(&stdin_ringbuf)) {
        for (uint8_t itf = 0; itf < 8; ++itf) {
            if (cdc_itf_pending & (1 << itf)) {
                tud_cdc_rx_cb(itf);
                if (!cdc_itf_pending) {
                    break;
                }
            }
        }
    }
    if ((poll_flags & MP_STREAM_POLL_RD) && ringbuf_peek(&stdin_ringbuf) != -1) {
        ret |= MP_STREAM_POLL_RD;
    }
    #endif
    if ((poll_flags & MP_STREAM_POLL_WR) &&
        (!tud_cdc_connected() || (tud_cdc_connected() && tud_cdc_write_available() > 0))) {
        // Always allow write when not connected, fifo will retain latest.
        // When connected operate as blocking, only allow if space is available.
        ret |= MP_STREAM_POLL_WR;
    }
    return ret;
}

void MICROPY_WRAP_TUD_CDC_RX_CB(tud_cdc_rx_cb)(uint8_t itf) {
    #if MICROPY_HW_USB_CDC_STREAM
    // Data stays in TinyUSB FIFO, read via USBD_CDC stream.
    (void)itf;
    #else
    // consume pending USB data immediately to free usb buffer and keep the endpoint from stalling.
    // in case the ringbuffer is full, mark the CDC interface that need attention later on for polling
    cdc_itf_pending &= ~(1 << itf);
    for (uint32_t bytes_avail = tud_cdc_n_available(itf); bytes_avail > 0; --bytes_avail) {
        if (ringbuf_free(&stdin_ringbuf)) {
            int data_char = tud_cdc_read_char();
            #if MICROPY_KBD_EXCEPTION
            if (data_char == mp_interrupt_char) {
                // Clear the ring buffer
                stdin_ringbuf.iget = stdin_ringbuf.iput = 0;
                // and stop
                mp_sched_keyboard_interrupt();
            } else {
                ringbuf_put(&stdin_ringbuf, data_char);
            }
            #else
            ringbuf_put(&stdin_ringbuf, data_char);
            #endif
        } else {
            cdc_itf_pending |= (1 << itf);
            return;
        }
    }
    #endif
}

#if MICROPY_HW_USB_CDC_STREAM

const machine_usbd_cdc_obj_t machine_usbd_cdc_obj = {{&machine_usbd_cdc_type}};

static mp_obj_t machine_usbd_cdc_make_new(const mp_obj_type_t *type, size_t n_args, size_t n_kw, const mp_obj_t *args) {
    (void)type;
    (void)args;
    mp_arg_check_num(n_args, n_kw, 0, 0, false);
    return MP_OBJ_FROM_PTR(&machine_usbd_cdc_obj);
}

static void machine_usbd_cdc_print(const mp_print_t *print, mp_obj_t self_in, mp_print_kind_t kind) {
    (void)self_in;
    (void)kind;
    mp_printf(print, "USBD_CDC()");
}

static mp_obj_t machine_usbd_cdc_any(mp_obj_t self_in) {
    (void)self_in;
    mp_usbd_task();
    return mp_obj_new_bool(tud_cdc_available());
}
static MP_DEFINE_CONST_FUN_OBJ_1(machine_usbd_cdc_any_obj, machine_usbd_cdc_any);

static mp_obj_t machine_usbd_cdc_isconnected(mp_obj_t self_in) {
    (void)self_in;
    return mp_obj_new_bool(tud_cdc_connected());
}
static MP_DEFINE_CONST_FUN_OBJ_1(machine_usbd_cdc_isconnected_obj, machine_usbd_cdc_isconnected);

static mp_uint_t machine_usbd_cdc_stream_read(mp_obj_t self_in, void *buf_in, mp_uint_t size, int *errcode) {
    (void)self_in;
    // Process pending USB events. Interrupt char (Ctrl-C) is detected by
    // tud_cdc_rx_wanted_cb() which fires during mp_usbd_task() before
    // tud_cdc_read() can return the data — guaranteed by TinyUSB's
    // cdcd_xfer_cb() which checks for wanted_char before tud_cdc_rx_cb().
    mp_usbd_task();
    uint32_t n = tud_cdc_read(buf_in, size);
    if (n == 0) {
        *errcode = MP_EAGAIN;
        return MP_STREAM_ERROR;
    }
    return n;
}

static mp_uint_t machine_usbd_cdc_stream_write(mp_obj_t self_in, const void *buf_in, mp_uint_t size, int *errcode) {
    (void)self_in;
    mp_uint_t n = mp_usbd_cdc_tx_strn((const char *)buf_in, size);
    if (n == 0) {
        *errcode = MP_EAGAIN;
        return MP_STREAM_ERROR;
    }
    return n;
}

static mp_uint_t machine_usbd_cdc_stream_ioctl(mp_obj_t self_in, mp_uint_t request, uintptr_t arg, int *errcode) {
    (void)self_in;
    if (request == MP_STREAM_POLL) {
        return mp_usbd_cdc_poll_interfaces(arg);
    } else if (request == MP_STREAM_CLOSE) {
        return 0;
    }
    *errcode = MP_EINVAL;
    return MP_STREAM_ERROR;
}

static const mp_stream_p_t machine_usbd_cdc_stream_p = {
    .read = machine_usbd_cdc_stream_read,
    .write = machine_usbd_cdc_stream_write,
    .ioctl = machine_usbd_cdc_stream_ioctl,
};

static const mp_rom_map_elem_t machine_usbd_cdc_locals_dict_table[] = {
    { MP_ROM_QSTR(MP_QSTR_read), MP_ROM_PTR(&mp_stream_read_obj) },
    { MP_ROM_QSTR(MP_QSTR_readinto), MP_ROM_PTR(&mp_stream_readinto_obj) },
    { MP_ROM_QSTR(MP_QSTR_readline), MP_ROM_PTR(&mp_stream_unbuffered_readline_obj) },
    { MP_ROM_QSTR(MP_QSTR_readlines), MP_ROM_PTR(&mp_stream_unbuffered_readlines_obj) },
    { MP_ROM_QSTR(MP_QSTR_write), MP_ROM_PTR(&mp_stream_write_obj) },
    { MP_ROM_QSTR(MP_QSTR_close), MP_ROM_PTR(&mp_identity_obj) },
    { MP_ROM_QSTR(MP_QSTR_any), MP_ROM_PTR(&machine_usbd_cdc_any_obj) },
    { MP_ROM_QSTR(MP_QSTR_isconnected), MP_ROM_PTR(&machine_usbd_cdc_isconnected_obj) },
    { MP_ROM_QSTR(MP_QSTR___del__), MP_ROM_PTR(&mp_identity_obj) },
    { MP_ROM_QSTR(MP_QSTR___enter__), MP_ROM_PTR(&mp_identity_obj) },
    { MP_ROM_QSTR(MP_QSTR___exit__), MP_ROM_PTR(&mp_stream___exit___obj) },
};

static MP_DEFINE_CONST_DICT(machine_usbd_cdc_locals_dict, machine_usbd_cdc_locals_dict_table);

MP_DEFINE_CONST_OBJ_TYPE(
    machine_usbd_cdc_type,
    MP_QSTR_USBD_CDC,
    MP_TYPE_FLAG_ITER_IS_STREAM,
    make_new, machine_usbd_cdc_make_new,
    print, machine_usbd_cdc_print,
    protocol, &machine_usbd_cdc_stream_p,
    locals_dict, &machine_usbd_cdc_locals_dict
    );

#if MICROPY_KBD_EXCEPTION
void tud_cdc_rx_wanted_cb(uint8_t itf, char wanted_char) {
    (void)itf;
    (void)wanted_char;
    tud_cdc_read_flush();
    mp_sched_keyboard_interrupt();
}
#endif

#endif // MICROPY_HW_USB_CDC_STREAM

mp_uint_t mp_usbd_cdc_tx_strn(const char *str, mp_uint_t len) {
    if (!tusb_inited()) {
        return 0;
    }
    mp_uint_t last_write = mp_hal_ticks_ms();
    size_t i = 0;
    while (i < len) {
        uint32_t n = len - i;

        if (tud_cdc_connected()) {
            // Limit write to available space in tx buffer when connected.
            //
            // (If not connected then we write everything to the fifo, expecting
            // it to overwrite old data so it will have latest data buffered
            // when host connects.)
            n = MIN(n, tud_cdc_write_available());
        }

        uint32_t n2 = tud_cdc_write(str + i, n);
        tud_cdc_write_flush();
        i += n2;

        if (i < len) {
            if (n2 > 0) {
                // reset the timeout each time we successfully write to the FIFO
                last_write = mp_hal_ticks_ms();
            } else {
                if ((mp_uint_t)(mp_hal_ticks_ms() - last_write) >= MICROPY_HW_USB_CDC_TX_TIMEOUT) {
                    break; // Timeout
                }

                if (tud_cdc_connected()) {
                    // If we know we're connected then we can wait for host to make
                    // more space
                    mp_event_wait_ms(1);
                }
            }

            // Always explicitly run the USB stack as the scheduler may be
            // locked (eg we are in an interrupt handler), while there is data
            // or a state change pending.
            mp_usbd_task();
        }
    }
    return i;
}

void MICROPY_WRAP_TUD_SOF_CB(tud_sof_cb)(uint32_t frame_count) {
    if (--cdc_connected_flush_delay < 0) {
        // Finished on-connection delay, disable SOF interrupt again.
        tud_sof_cb_enable(false);
        tud_cdc_write_flush();
    }
}

#endif

#if MICROPY_HW_ENABLE_USBDEV && ( \
    MICROPY_HW_USB_CDC_1200BPS_TOUCH || \
    MICROPY_HW_USB_CDC || \
    MICROPY_HW_USB_CDC_DTR_RTS_BOOTLOADER)

#if MICROPY_HW_USB_CDC_1200BPS_TOUCH || MICROPY_HW_USB_CDC_DTR_RTS_BOOTLOADER
static mp_sched_node_t mp_bootloader_sched_node;

static void usbd_cdc_run_bootloader_task(mp_sched_node_t *node) {
    mp_hal_delay_ms(250);
    machine_bootloader(0, NULL);
}
#endif

#if MICROPY_HW_USB_CDC_DTR_RTS_BOOTLOADER
static struct {
    bool dtr : 1;
    bool rts : 1;
} prev_line_state = {0};
#endif

void MICROPY_WRAP_TUD_CDC_LINE_STATE_CB(tud_cdc_line_state_cb)(uint8_t itf, bool dtr, bool rts) {
    #if MICROPY_HW_USB_CDC && !MICROPY_EXCLUDE_SHARED_TINYUSB_USBD_CDC
    if (dtr) {
        // A host application has started to open the cdc serial port.
        // Wait a few ms for host to be ready then send tx buffer.
        // High speed connection SOF fires at 125us, full speed at 1ms.
        cdc_connected_flush_delay = (tud_speed_get() == TUSB_SPEED_HIGH) ? 128 : 16;
        tud_sof_cb_enable(true);
    }
    #endif
    #if MICROPY_HW_USB_CDC_DTR_RTS_BOOTLOADER
    if (dtr && !rts) {
        if (prev_line_state.rts && !prev_line_state.dtr) {
            mp_sched_schedule_node(&mp_bootloader_sched_node, usbd_cdc_run_bootloader_task);
        }
    }
    prev_line_state.rts = rts;
    prev_line_state.dtr = dtr;
    #endif
    #if MICROPY_HW_USB_CDC_1200BPS_TOUCH
    if (dtr == false && rts == false) {
        // Device is disconnected.
        cdc_line_coding_t line_coding;
        tud_cdc_n_get_line_coding(itf, &line_coding);
        if (line_coding.bit_rate == 1200) {
            // Delay bootloader jump to allow the USB stack to service endpoints.
            mp_sched_schedule_node(&mp_bootloader_sched_node, usbd_cdc_run_bootloader_task);
        }
    }
    #endif
}

#endif
