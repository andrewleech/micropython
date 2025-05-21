/*
 * This file is part of the MicroPython project, http://micropython.org/
 *
 * The MIT License (MIT)
 *
 * Copyright (c) 2014-2019 Damien P. George
 * Copyright (c) 2024 MicroPython Contributors
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

#include <stdio.h>
#include <string.h>

#include "py/objlist.h"
#include "py/runtime.h"
#include "py/stream.h"

#if MICROPY_PY_TOML

#include "lib/tomlc17/src/tomlc17.h"

// Forward declaration
static mp_obj_t toml_datum_to_mp_obj(const toml_datum_t *datum);

// Convert a TOML datum to a MicroPython object (simpler implementation)
static mp_obj_t toml_datum_to_mp_obj(const toml_datum_t *datum) {
    if (datum == NULL) {
        return mp_const_none;
    }

    switch (datum->type) {
        case TOML_STRING:
            return mp_obj_new_str(datum->u.str.ptr, datum->u.str.len);

        case TOML_INT64:
            return mp_obj_new_int_from_ll(datum->u.int64);

        case TOML_FP64:
            #if MICROPY_FLOAT_IMPL != MICROPY_FLOAT_IMPL_NONE
            return mp_obj_new_float((mp_float_t)datum->u.fp64);
            #else
            return mp_obj_new_int_from_ll((mp_int_t)datum->u.fp64);
            #endif

        case TOML_BOOLEAN:
            return mp_obj_new_bool(datum->u.boolean);

        case TOML_TABLE: {
            // Create a new dict for the table
            mp_obj_t dict = mp_obj_new_dict(0);

            // Process each key-value pair in the table
            for (int i = 0; i < datum->u.tab.size; i++) {
                const char *key = datum->u.tab.key[i];
                int key_len = datum->u.tab.len[i];
                toml_datum_t *value = &datum->u.tab.value[i];

                // Convert the key and value to MicroPython objects
                mp_obj_t key_obj = mp_obj_new_str(key, key_len);
                mp_obj_t value_obj = toml_datum_to_mp_obj(value);

                // Store the key-value pair in the dict
                mp_obj_dict_store(dict, key_obj, value_obj);
            }

            return dict;
        }

        case TOML_ARRAY: {
            // Create a new list for the array
            mp_obj_t list = mp_obj_new_list(0, NULL);

            // Process each element in the array
            for (int i = 0; i < datum->u.arr.size; i++) {
                toml_datum_t *elem = &datum->u.arr.elem[i];

                // Convert the element to a MicroPython object
                mp_obj_t elem_obj = toml_datum_to_mp_obj(elem);

                // Append the element to the list
                mp_obj_list_append(list, elem_obj);
            }

            return list;
        }

        default:
            // Unknown or unsupported type, return None
            return mp_const_none;
    }
}

// loads(s) function
static mp_obj_t mod_toml_loads(mp_obj_t obj) {
    mp_buffer_info_t bufinfo;
    mp_get_buffer_raise(obj, &bufinfo, MP_BUFFER_READ);

    // Parse TOML data
    toml_result_t result = toml_parse(bufinfo.buf, bufinfo.len);

    if (!result.ok) {
        mp_raise_ValueError(MP_ERROR_TEXT("TOML syntax error"));
    }

    // Convert the top-level table to a Python dict
    mp_obj_t dict = toml_datum_to_mp_obj(&result.toptab);

    // Free the parsed TOML data
    toml_free(result);

    return dict;
}
static MP_DEFINE_CONST_FUN_OBJ_1(mod_toml_loads_obj, mod_toml_loads);

static const mp_rom_map_elem_t mp_module_toml_globals_table[] = {
    { MP_ROM_QSTR(MP_QSTR___name__), MP_ROM_QSTR(MP_QSTR_toml) },
    { MP_ROM_QSTR(MP_QSTR_loads), MP_ROM_PTR(&mod_toml_loads_obj) },
};

static MP_DEFINE_CONST_DICT(mp_module_toml_globals, mp_module_toml_globals_table);

const mp_obj_module_t mp_module_toml = {
    .base = { &mp_type_module },
    .globals = (mp_obj_dict_t *)&mp_module_toml_globals,
};

MP_REGISTER_MODULE(MP_QSTR_toml, mp_module_toml);

#endif // MICROPY_PY_TOML
