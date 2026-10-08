/*
 * This file is part of the MicroPython project, http://micropython.org/
 *
 * The MIT License (MIT)
 *
 * Copyright (c) 2026 Andrew Leech
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
#include "py/mperrno.h"

#if MICROPY_PY_MBOOT

#include <errno.h>
#include <string.h>

#include "shared/mboot/include/mboot_app.h"
#include "mboot_layout.h"

// Element types of the bootloader element stream, and the filesystem types of a MOUNT element.
#define ELEM_END        1
#define ELEM_MOUNT      2
#define ELEM_FSLOAD     3
#define FS_FAT          1
#define FS_LFS2         3
#define FS_RAW          4

// Mount point number used by request_fsload().
#define FSLOAD_MOUNT_POINT  1

#define FSLOAD_PATH_MAX     254

// Filesystem type of the application filesystem area when request_fsload() is called without
// mount: FAT, unless the board's bootloader only has the littlefs2 reader.
#if defined(MBOOT_FSLOAD_LFS2) && !defined(MBOOT_FSLOAD_FAT)
#define DEFAULT_FS_TYPE FS_LFS2
#else
#define DEFAULT_FS_TYPE FS_FAT
#endif

typedef struct _mboot_writer_obj_t {
    mp_obj_base_t base;
    uint32_t generation;
    mboot_app_writer_t writer;
} mboot_writer_obj_t;

// Opening a Writer invalidates earlier ones, only the newest may touch the slot.
static uint32_t writer_generation;

// Raise the OSError for a negative errno value returned by the glue layer.
static MP_NORETURN void raise_err(int rc) {
    int err;
    switch (-rc) {
        case EPERM:
            err = MP_EPERM;
            break;
        case ENOENT:
            err = MP_ENOENT;
            break;
        case EBADF:
            err = MP_EBADF;
            break;
        case ENODEV:
            err = MP_ENODEV;
            break;
        case EINVAL:
            err = MP_EINVAL;
            break;
        case ENOSPC:
            err = MP_ENOSPC;
            break;
        default:
            err = MP_EIO;
            break;
    }
    mp_raise_OSError(err);
}

static void check_rc(int rc) {
    if (rc < 0) {
        raise_err(rc);
    }
}

static mp_obj_t version_tuple(const mboot_app_version_t *v) {
    mp_obj_t items[4] = {
        MP_OBJ_NEW_SMALL_INT(v->major),
        MP_OBJ_NEW_SMALL_INT(v->minor),
        MP_OBJ_NEW_SMALL_INT(v->revision),
        mp_obj_new_int_from_uint(v->build),
    };
    return mp_obj_new_tuple(4, items);
}

static void dict_store(mp_obj_t dict, qstr key, mp_obj_t value) {
    mp_obj_dict_store(dict, MP_OBJ_NEW_QSTR(key), value);
}

// mboot.version(slot=0)
static mp_obj_t mboot_version(size_t n_args, const mp_obj_t *pos_args, mp_map_t *kw_args) {
    static const mp_arg_t allowed_args[] = {
        { MP_QSTR_slot, MP_ARG_INT, {.u_int = MBOOT_APP_SLOT_PRIMARY} },
    };
    mp_arg_val_t args[MP_ARRAY_SIZE(allowed_args)];
    mp_arg_parse_all(n_args, pos_args, kw_args, MP_ARRAY_SIZE(allowed_args), allowed_args, args);
    mp_int_t slot = args[0].u_int;
    if (slot != MBOOT_APP_SLOT_PRIMARY && slot != MBOOT_APP_SLOT_SECONDARY) {
        mp_raise_ValueError(MP_ERROR_TEXT("slot must be 0 or 1"));
    }
    mboot_app_version_t v;
    int rc = mboot_app_version(slot, &v);
    if (rc == -ENOENT) {
        return mp_const_none;
    }
    check_rc(rc);
    return version_tuple(&v);
}
static MP_DEFINE_CONST_FUN_OBJ_KW(mboot_version_obj, 0, mboot_version);

// mboot.bootloader_info()
static mp_obj_t mboot_bootloader_info(void) {
    mboot_app_bl_info_t info;
    if (mboot_app_bootloader_info(&info) != 0) {
        return mp_const_none;
    }
    mp_obj_t d = mp_obj_new_dict(3);
    dict_store(d, MP_QSTR_version, mp_obj_new_str(info.version, strlen(info.version)));
    dict_store(d, MP_QSTR_layout_id, mp_obj_new_int_from_uint(info.layout_id));
    dict_store(d, MP_QSTR_api, mp_obj_new_int_from_uint(info.api));
    return d;
}
static MP_DEFINE_CONST_FUN_OBJ_0(mboot_bootloader_info_obj, mboot_bootloader_info);

// mboot.state()
static mp_obj_t mboot_state(void) {
    mboot_app_state_t s;
    check_rc(mboot_app_state(&s));
    mp_obj_t d = mp_obj_new_dict(5);
    dict_store(d, MP_QSTR_swap, MP_OBJ_NEW_SMALL_INT(s.swap));
    dict_store(d, MP_QSTR_confirmed, mp_obj_new_bool(s.confirmed));
    dict_store(d, MP_QSTR_pending, mp_obj_new_bool(s.pending));
    dict_store(d, MP_QSTR_secondary_valid_header, mp_obj_new_bool(s.secondary_valid_header));
    dict_store(d, MP_QSTR_layout_mismatch, mp_obj_new_bool(s.layout_mismatch));
    return d;
}
static MP_DEFINE_CONST_FUN_OBJ_0(mboot_state_obj, mboot_state);

// mboot.confirm()
static mp_obj_t mboot_confirm(void) {
    check_rc(mboot_app_confirm());
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_0(mboot_confirm_obj, mboot_confirm);

// mboot.request_upgrade(permanent=False)
static mp_obj_t mboot_request_upgrade(size_t n_args, const mp_obj_t *pos_args, mp_map_t *kw_args) {
    static const mp_arg_t allowed_args[] = {
        { MP_QSTR_permanent, MP_ARG_BOOL, {.u_bool = false} },
    };
    mp_arg_val_t args[MP_ARRAY_SIZE(allowed_args)];
    mp_arg_parse_all(n_args, pos_args, kw_args, MP_ARRAY_SIZE(allowed_args), allowed_args, args);
    check_rc(mboot_app_request_upgrade(args[0].u_bool));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_KW(mboot_request_upgrade_obj, 0, mboot_request_upgrade);

// mboot.reset()
static mp_obj_t mboot_reset(void) {
    mboot_app_reset();
}
static MP_DEFINE_CONST_FUN_OBJ_0(mboot_reset_obj, mboot_reset);

// mboot.request_dfu()
static mp_obj_t mboot_request_dfu(void) {
    mboot_app_request_dfu();
}
static MP_DEFINE_CONST_FUN_OBJ_0(mboot_request_dfu_obj, mboot_request_dfu);

static void put_le32(uint8_t *p, uint32_t v) {
    p[0] = v;
    p[1] = v >> 8;
    p[2] = v >> 16;
    p[3] = v >> 24;
}

// mboot.request_fsload(path, mount=None)
//
// mount is None (the application filesystem area of the flash map, type DEFAULT_FS_TYPE) or a
// sequence (fs_type, base, len[, arg2[, arg3]]), the payload of a MOUNT element (fs_type, then 2
// to 4 u32 values) with base and len as DFU addresses.
static mp_obj_t mboot_request_fsload(size_t n_args, const mp_obj_t *pos_args, mp_map_t *kw_args) {
    static const mp_arg_t allowed_args[] = {
        { MP_QSTR_path, MP_ARG_REQUIRED | MP_ARG_OBJ, {.u_rom_obj = MP_ROM_NONE} },
        { MP_QSTR_mount, MP_ARG_OBJ, {.u_rom_obj = MP_ROM_NONE} },
    };
    mp_arg_val_t args[MP_ARRAY_SIZE(allowed_args)];
    mp_arg_parse_all(n_args, pos_args, kw_args, MP_ARRAY_SIZE(allowed_args), allowed_args, args);
    mp_obj_t mount_obj = args[1].u_obj;
    size_t path_len;
    const char *path = mp_obj_str_get_data(args[0].u_obj, &path_len);
    if (path_len == 0 || path_len > FSLOAD_PATH_MAX) {
        mp_raise_ValueError(MP_ERROR_TEXT("path length must be 1 to 254"));
    }
    for (size_t i = 0; i < path_len; ++i) {
        if (path[i] == '\0' || (path[i] & 0x80)) {
            mp_raise_ValueError(MP_ERROR_TEXT("path must be ASCII without NUL"));
        }
    }

    uint32_t fs_type = DEFAULT_FS_TYPE;
    uint32_t mount_args[4];   // base, len, arg2, arg3
    size_t n_mount = 2;
    if (mount_obj == mp_const_none) {
        check_rc(mboot_app_fs_area(&mount_args[0], &mount_args[1]));
    } else {
        size_t n;
        mp_obj_t *items;
        mp_obj_get_array(mount_obj, &n, &items);
        if (n < 3 || n > 5) {
            mp_raise_ValueError(MP_ERROR_TEXT("mount must be (fs_type, base, len[, arg2[, arg3]])"));
        }
        fs_type = mp_obj_get_uint(items[0]);
        if (fs_type != FS_FAT && fs_type != FS_LFS2 && fs_type != FS_RAW) {
            mp_raise_ValueError(MP_ERROR_TEXT("unsupported fs_type"));
        }
        n_mount = n - 1;
        for (size_t i = 0; i < n_mount; ++i) {
            mp_uint_t v = mp_obj_get_uint(items[i + 1]);
            // mp_obj_get_uint() rejects values that do not fit an mp_uint_t, which is already
            // the range of the u32 field when mp_uint_t is 32 bits.
            #if SIZE_MAX > 0xffffffffu
            if (v > 0xffffffffu) {
                mp_raise_ValueError(MP_ERROR_TEXT("value out of range"));
            }
            #endif
            mount_args[i] = v;
        }
    }

    // MOUNT: mount_point, fs_type, then 2 to 4 u32. FSLOAD: mount_point, path. END.
    uint8_t elems[2 + 2 + 4 * 4 + 2 + 1 + FSLOAD_PATH_MAX + 2];
    uint8_t *p = elems;
    *p++ = ELEM_MOUNT;
    *p++ = 2 + 4 * n_mount;
    *p++ = FSLOAD_MOUNT_POINT;
    *p++ = fs_type;
    for (size_t i = 0; i < n_mount; ++i) {
        put_le32(p, mount_args[i]);
        p += 4;
    }
    *p++ = ELEM_FSLOAD;
    *p++ = 1 + path_len;
    *p++ = FSLOAD_MOUNT_POINT;
    memcpy(p, path, path_len);
    p += path_len;
    *p++ = ELEM_END;
    *p++ = 0;

    check_rc(mboot_app_request_fsload(elems, p - elems));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_KW(mboot_request_fsload_obj, 1, mboot_request_fsload);

// mboot.log(n=None)
static mp_obj_t mboot_log(size_t n_args, const mp_obj_t *pos_args, mp_map_t *kw_args) {
    static const mp_arg_t allowed_args[] = {
        { MP_QSTR_n, MP_ARG_OBJ, {.u_rom_obj = MP_ROM_NONE} },
    };
    mp_arg_val_t args[MP_ARRAY_SIZE(allowed_args)];
    mp_arg_parse_all(n_args, pos_args, kw_args, MP_ARRAY_SIZE(allowed_args), allowed_args, args);
    mp_uint_t limit = (mp_uint_t)-1;
    if (args[0].u_obj != mp_const_none) {
        mp_int_t n = mp_obj_get_int(args[0].u_obj);
        if (n < 0) {
            mp_raise_ValueError(MP_ERROR_TEXT("n must not be negative"));
        }
        limit = n;
    }
    mp_obj_t list = mp_obj_new_list(0, NULL);
    for (mp_uint_t i = 0; i < limit; ++i) {
        mboot_app_log_entry_t e;
        int rc = mboot_app_log_get(i, &e);
        if (rc == -ENOENT) {
            break;
        }
        check_rc(rc);
        mp_obj_t items[6] = {
            mp_obj_new_int_from_uint(e.seq),
            MP_OBJ_NEW_SMALL_INT(e.type),
            MP_OBJ_NEW_SMALL_INT(e.result),
            MP_OBJ_NEW_SMALL_INT(e.source),
            version_tuple(&e.version),
            mp_obj_new_int_from_uint(e.detail),
        };
        mp_obj_list_append(list, mp_obj_new_tuple(6, items));
    }
    return list;
}
static MP_DEFINE_CONST_FUN_OBJ_KW(mboot_log_obj, 0, mboot_log);

// mboot.slots()
static mp_obj_t mboot_slots(void) {
    size_t count = mboot_app_slot_count();
    mp_obj_t list = mp_obj_new_list(0, NULL);
    for (size_t i = 0; i < count; ++i) {
        mboot_app_slot_t s;
        check_rc(mboot_app_slot_get(i, &s));
        mp_obj_t items[3] = {
            mp_obj_new_str(s.name, strlen(s.name)),
            mp_obj_new_int_from_uint(s.start),
            mp_obj_new_int_from_uint(s.size),
        };
        mp_obj_list_append(list, mp_obj_new_tuple(3, items));
    }
    return list;
}
static MP_DEFINE_CONST_FUN_OBJ_0(mboot_slots_obj, mboot_slots);

// ---- Writer ----

static mboot_writer_obj_t *writer_get(mp_obj_t self_in) {
    mboot_writer_obj_t *self = MP_OBJ_TO_PTR(self_in);
    if (self->generation != writer_generation) {
        // A newer Writer has been opened.
        mp_raise_OSError(MP_EBADF);
    }
    return self;
}

static mp_obj_t mboot_writer_make_new(const mp_obj_type_t *type, size_t n_args, size_t n_kw, const mp_obj_t *all_args) {
    enum { ARG_permanent };
    static const mp_arg_t allowed_args[] = {
        { MP_QSTR_permanent, MP_ARG_BOOL, {.u_bool = false} },
    };
    mp_arg_val_t args[MP_ARRAY_SIZE(allowed_args)];
    mp_arg_parse_all_kw_array(n_args, n_kw, all_args, MP_ARRAY_SIZE(allowed_args), allowed_args, args);

    mboot_writer_obj_t *self = mp_obj_malloc(mboot_writer_obj_t, type);
    int rc = mboot_app_writer_open(&self->writer, args[ARG_permanent].u_bool);
    if (rc != 0) {
        // The slot may have been partly erased, so no earlier Writer remains usable.
        ++writer_generation;
        raise_err(rc);
    }
    self->generation = ++writer_generation;
    return MP_OBJ_FROM_PTR(self);
}

static mp_obj_t mboot_writer_write(mp_obj_t self_in, mp_obj_t buf_in) {
    mboot_writer_obj_t *self = writer_get(self_in);
    mp_buffer_info_t bufinfo;
    mp_get_buffer_raise(buf_in, &bufinfo, MP_BUFFER_READ);
    check_rc(mboot_app_writer_write(&self->writer, bufinfo.buf, bufinfo.len));
    return mp_obj_new_int_from_uint(bufinfo.len);
}
static MP_DEFINE_CONST_FUN_OBJ_2(mboot_writer_write_obj, mboot_writer_write);

static mp_obj_t mboot_writer_finish(mp_obj_t self_in) {
    mboot_writer_obj_t *self = writer_get(self_in);
    uint32_t total;
    check_rc(mboot_app_writer_finish(&self->writer, &total));
    return mp_obj_new_int_from_uint(total);
}
static MP_DEFINE_CONST_FUN_OBJ_1(mboot_writer_finish_obj, mboot_writer_finish);

static mp_obj_t mboot_writer_abort(mp_obj_t self_in) {
    mboot_writer_obj_t *self = writer_get(self_in);
    check_rc(mboot_app_writer_abort(&self->writer));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(mboot_writer_abort_obj, mboot_writer_abort);

// Leaving the with block finishes the update, or aborts it if an exception is in flight. A Writer
// already finished or aborted inside the block is left as it is.
static mp_obj_t mboot_writer_exit(size_t n_args, const mp_obj_t *args) {
    mboot_writer_obj_t *self = writer_get(args[0]);
    bool failed = n_args > 1 && args[1] != mp_const_none;
    int rc;
    if (failed) {
        rc = mboot_app_writer_abort(&self->writer);
    } else {
        uint32_t total;
        rc = mboot_app_writer_finish(&self->writer, &total);
    }
    if (rc != -EBADF) {
        check_rc(rc);
    }
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(mboot_writer_exit_obj, 1, 4, mboot_writer_exit);

static const mp_rom_map_elem_t mboot_writer_locals_dict_table[] = {
    { MP_ROM_QSTR(MP_QSTR_write), MP_ROM_PTR(&mboot_writer_write_obj) },
    { MP_ROM_QSTR(MP_QSTR_finish), MP_ROM_PTR(&mboot_writer_finish_obj) },
    { MP_ROM_QSTR(MP_QSTR_abort), MP_ROM_PTR(&mboot_writer_abort_obj) },
    { MP_ROM_QSTR(MP_QSTR___enter__), MP_ROM_PTR(&mp_identity_obj) },
    { MP_ROM_QSTR(MP_QSTR___exit__), MP_ROM_PTR(&mboot_writer_exit_obj) },
};
static MP_DEFINE_CONST_DICT(mboot_writer_locals_dict, mboot_writer_locals_dict_table);

static MP_DEFINE_CONST_OBJ_TYPE(
    mboot_writer_type,
    MP_QSTR_Writer,
    MP_TYPE_FLAG_NONE,
    make_new, mboot_writer_make_new,
    locals_dict, &mboot_writer_locals_dict
    );

static const mp_rom_map_elem_t mboot_module_globals_table[] = {
    { MP_ROM_QSTR(MP_QSTR___name__), MP_ROM_QSTR(MP_QSTR_mboot) },
    { MP_ROM_QSTR(MP_QSTR_version), MP_ROM_PTR(&mboot_version_obj) },
    { MP_ROM_QSTR(MP_QSTR_bootloader_info), MP_ROM_PTR(&mboot_bootloader_info_obj) },
    { MP_ROM_QSTR(MP_QSTR_state), MP_ROM_PTR(&mboot_state_obj) },
    { MP_ROM_QSTR(MP_QSTR_confirm), MP_ROM_PTR(&mboot_confirm_obj) },
    { MP_ROM_QSTR(MP_QSTR_request_upgrade), MP_ROM_PTR(&mboot_request_upgrade_obj) },
    { MP_ROM_QSTR(MP_QSTR_reset), MP_ROM_PTR(&mboot_reset_obj) },
    { MP_ROM_QSTR(MP_QSTR_request_dfu), MP_ROM_PTR(&mboot_request_dfu_obj) },
    { MP_ROM_QSTR(MP_QSTR_request_fsload), MP_ROM_PTR(&mboot_request_fsload_obj) },
    { MP_ROM_QSTR(MP_QSTR_log), MP_ROM_PTR(&mboot_log_obj) },
    { MP_ROM_QSTR(MP_QSTR_slots), MP_ROM_PTR(&mboot_slots_obj) },
    { MP_ROM_QSTR(MP_QSTR_Writer), MP_ROM_PTR(&mboot_writer_type) },
    { MP_ROM_QSTR(MP_QSTR_SWAP_NONE), MP_ROM_INT(MBOOT_APP_SWAP_NONE) },
    { MP_ROM_QSTR(MP_QSTR_SWAP_TEST), MP_ROM_INT(MBOOT_APP_SWAP_TEST) },
    { MP_ROM_QSTR(MP_QSTR_SWAP_PERM), MP_ROM_INT(MBOOT_APP_SWAP_PERM) },
    { MP_ROM_QSTR(MP_QSTR_SWAP_REVERT), MP_ROM_INT(MBOOT_APP_SWAP_REVERT) },
    { MP_ROM_QSTR(MP_QSTR_SWAP_FAIL), MP_ROM_INT(MBOOT_APP_SWAP_FAIL) },
    { MP_ROM_QSTR(MP_QSTR_FS_FAT), MP_ROM_INT(FS_FAT) },
    { MP_ROM_QSTR(MP_QSTR_FS_LFS2), MP_ROM_INT(FS_LFS2) },
    { MP_ROM_QSTR(MP_QSTR_FS_RAW), MP_ROM_INT(FS_RAW) },
};
static MP_DEFINE_CONST_DICT(mboot_module_globals, mboot_module_globals_table);

const mp_obj_module_t mp_module_mboot = {
    .base = { &mp_type_module },
    .globals = (mp_obj_dict_t *)&mboot_module_globals,
};

MP_REGISTER_MODULE(MP_QSTR_mboot, mp_module_mboot);

#endif // MICROPY_PY_MBOOT
