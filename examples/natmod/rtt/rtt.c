/*
 * RTT (Real-Time Transfer) Native Module for MicroPython
 *
 * This module provides a stream interface to SEGGER's RTT library,
 * enabling fast, non-blocking debug communication through J-Link
 * and other compatible debug probes.
 */

#include "py/dynruntime.h"

// Include SEGGER RTT library
#include "SEGGER_RTT.h"

// RTT Stream object type
mp_obj_full_type_t rtt_stream_type;

// RTT Stream instance structure
typedef struct _rtt_stream_obj_t {
    mp_obj_base_t base;
    unsigned channel;
    bool initialized;
} rtt_stream_obj_t;

// Forward declarations for stream protocol
static mp_uint_t rtt_stream_read(mp_obj_t self_in, void *buf, mp_uint_t size, int *errcode);
static mp_uint_t rtt_stream_write(mp_obj_t self_in, const void *buf, mp_uint_t size, int *errcode);
static mp_uint_t rtt_stream_ioctl(mp_obj_t self_in, mp_uint_t request, uintptr_t arg, int *errcode);

// Stream protocol implementation
static const mp_stream_p_t rtt_stream_p = {
    .read = rtt_stream_read,
    .write = rtt_stream_write,
    .ioctl = rtt_stream_ioctl,
};

// RTT Stream read implementation
static mp_uint_t rtt_stream_read(mp_obj_t self_in, void *buf, mp_uint_t size, int *errcode) {
    rtt_stream_obj_t *self = MP_OBJ_TO_PTR(self_in);

    if (!self->initialized) {
        SEGGER_RTT_Init();
        self->initialized = true;
    }

    // RTT read is non-blocking, returns actual bytes read
    unsigned bytes_read = SEGGER_RTT_Read(self->channel, buf, size);

    return bytes_read;
}

// RTT Stream write implementation
static mp_uint_t rtt_stream_write(mp_obj_t self_in, const void *buf, mp_uint_t size, int *errcode) {
    rtt_stream_obj_t *self = MP_OBJ_TO_PTR(self_in);

    if (!self->initialized) {
        SEGGER_RTT_Init();
        self->initialized = true;
    }

    // RTT write returns number of bytes actually written
    unsigned bytes_written = SEGGER_RTT_Write(self->channel, buf, size);

    return bytes_written;
}

// RTT Stream ioctl implementation
static mp_uint_t rtt_stream_ioctl(mp_obj_t self_in, mp_uint_t request, uintptr_t arg, int *errcode) {
    rtt_stream_obj_t *self = MP_OBJ_TO_PTR(self_in);

    switch (request) {
        case MP_STREAM_POLL: {
            // Check if data is available for reading
            mp_uint_t ret = 0;
            if (arg & MP_STREAM_POLL_RD) {
                if (SEGGER_RTT_HasData(self->channel)) {
                    ret |= MP_STREAM_POLL_RD;
                }
            }
            if (arg & MP_STREAM_POLL_WR) {
                // RTT is generally always writable (buffered)
                ret |= MP_STREAM_POLL_WR;
            }
            return ret;
        }

        case MP_STREAM_CLOSE:
            // RTT doesn't need explicit closing
            return 0;

        default:
            *errcode = MP_EINVAL;
            return MP_STREAM_ERROR;
    }
}

// RTT Stream constructor
static mp_obj_t rtt_stream_make_new(const mp_obj_type_t *type, size_t n_args, size_t n_kw, const mp_obj_t *args) {
    // Parse arguments: RTTStream([channel])
    mp_arg_check_num(n_args, n_kw, 0, 1, false);

    // Create the stream object
    rtt_stream_obj_t *self = mp_obj_malloc(rtt_stream_obj_t, type);

    // Set channel (default to 0 - terminal channel)
    self->channel = (n_args > 0) ? mp_obj_get_int(args[0]) : 0;
    self->initialized = false;

    return MP_OBJ_FROM_PTR(self);
}

// RTT initialization function
static mp_obj_t rtt_init(void) {
    SEGGER_RTT_Init();
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_0(rtt_init_obj, rtt_init);

// Helper function to get available data count
static mp_obj_t rtt_has_data(size_t n_args, const mp_obj_t *args) {
    unsigned channel = (n_args > 0) ? mp_obj_get_int(args[0]) : 0;
    unsigned count = SEGGER_RTT_HasData(channel);
    return mp_obj_new_int(count);
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(rtt_has_data_obj, 0, 1, rtt_has_data);

// Helper function to get write space
static mp_obj_t rtt_write_space(size_t n_args, const mp_obj_t *args) {
    unsigned channel = (n_args > 0) ? mp_obj_get_int(args[0]) : 0;
    unsigned space = SEGGER_RTT_GetAvailWriteSpace(channel);
    return mp_obj_new_int(space);
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(rtt_write_space_obj, 0, 1, rtt_write_space);

// Stream protocol functions (re-implemented for dynamic modules)
static mp_obj_t rtt_stream_close(mp_obj_t self_in) {
    // RTT doesn't need explicit closing, but we implement for completeness
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(rtt_stream_close_obj, rtt_stream_close);

static mp_obj_t rtt_stream_enter(mp_obj_t self_in) {
    return self_in;
}
static MP_DEFINE_CONST_FUN_OBJ_1(rtt_stream_enter_obj, rtt_stream_enter);

static mp_obj_t rtt_stream_exit(size_t n_args, const mp_obj_t *args) {
    return rtt_stream_close(args[0]);
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(rtt_stream_exit_obj, 4, 4, rtt_stream_exit);

// RTT Stream locals dictionary
static const mp_rom_map_elem_t rtt_stream_locals_table[] = {
    { MP_ROM_QSTR(MP_QSTR_read), MP_ROM_PTR(&mp_stream_read_obj) },
    { MP_ROM_QSTR(MP_QSTR_readinto), MP_ROM_PTR(&mp_stream_readinto_obj) },
    { MP_ROM_QSTR(MP_QSTR_readline), MP_ROM_PTR(&mp_stream_unbuffered_readline_obj) },
    { MP_ROM_QSTR(MP_QSTR_write), MP_ROM_PTR(&mp_stream_write_obj) },
    { MP_ROM_QSTR(MP_QSTR_close), MP_ROM_PTR(&rtt_stream_close_obj) },
    { MP_ROM_QSTR(MP_QSTR___enter__), MP_ROM_PTR(&rtt_stream_enter_obj) },
    { MP_ROM_QSTR(MP_QSTR___exit__), MP_ROM_PTR(&rtt_stream_exit_obj) },
};
static MP_DEFINE_CONST_DICT(rtt_stream_locals_dict, rtt_stream_locals_table);

// Module initialization using traditional approach
// (Static module system doesn't handle type setup yet)
mp_obj_t mpy_init(mp_obj_fun_bc_t *self, size_t n_args, size_t n_kw, mp_obj_t *args) {
    MP_DYNRUNTIME_INIT_ENTRY

    // Initialize RTT Stream type
    rtt_stream_type.base.type = mp_fun_table.type_type;
    rtt_stream_type.name = MP_QSTR_RTTStream;
    MP_OBJ_TYPE_SET_SLOT(&rtt_stream_type, make_new, &rtt_stream_make_new, 0);
    MP_OBJ_TYPE_SET_SLOT(&rtt_stream_type, protocol, &rtt_stream_p, 1);
    MP_OBJ_TYPE_SET_SLOT(&rtt_stream_type, locals_dict, (void*)&rtt_stream_locals_dict, 2);

    // Register module globals
    mp_store_global(MP_QSTR___name__, MP_OBJ_NEW_QSTR(MP_QSTR_rtt));
    mp_store_global(MP_QSTR_RTTStream, MP_OBJ_FROM_PTR(&rtt_stream_type));
    mp_store_global(MP_QSTR_init, MP_OBJ_FROM_PTR(&rtt_init_obj));
    mp_store_global(MP_QSTR_has_data, MP_OBJ_FROM_PTR(&rtt_has_data_obj));
    mp_store_global(MP_QSTR_write_space, MP_OBJ_FROM_PTR(&rtt_write_space_obj));

    MP_DYNRUNTIME_INIT_EXIT
}