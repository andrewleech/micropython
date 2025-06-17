/*
 * This file is part of the MicroPython project, http://micropython.org/
 *
 * The MIT License (MIT)
 *
 * Copyright (c) 2025 Contributors to the MicroPython project
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

#include "py/mpprint.h"
#include "py/qstr.h"
#include "py/obj.h"
#include "py/bc.h"
#include "py/objfun.h"
#include "py/scope.h"
#include "py/runtime.h"
#include "py/profile.h"

#if MICROPY_PY_SYS_SETTRACE_SAVE_NAMES
#include "py/localnames.h"

// Debug function to print the actual local variable assignments
void mp_debug_print_local_variables(const mp_raw_code_t *rc, const mp_obj_t *state, uint16_t n_state) {
    mp_printf(&mp_plat_print, "DEBUG: Local variable mapping for %p\n", rc);

    // First print by ordering information
    mp_printf(&mp_plat_print, "DEBUG: Variables in source order (order_count=%d):\n", rc->local_names.order_count);
    for (uint16_t idx = 0; idx < rc->local_names.order_count; idx++) {
        uint16_t local_num = mp_local_names_get_local_num(&rc->local_names, idx);
        if (local_num == UINT16_MAX) {
            continue;
        }

        qstr name = mp_local_names_get_name(&rc->local_names, local_num);
        mp_printf(&mp_plat_print, "  [%d] local_num=%d, name=%q = ", idx, local_num, name);
        if (local_num < n_state && state[local_num] != MP_OBJ_NULL) {
            mp_obj_print(state[local_num], PRINT_REPR);
        } else {
            mp_printf(&mp_plat_print, "NULL or out of range");
        }
        mp_printf(&mp_plat_print, "\n");
    }

    // Print the direct mapping from local_num to name
    mp_printf(&mp_plat_print, "DEBUG: Direct local_num to name mapping:\n");
    for (uint16_t i = 0; i < MICROPY_PY_SYS_SETTRACE_NAMES_MAX && i < n_state; i++) {
        qstr name = mp_local_names_get_name(&rc->local_names, i);
        if (name != MP_QSTRnull) {
            mp_printf(&mp_plat_print, "  local_num %d = %q (", i, name);
            if (i < n_state && state[i] != MP_OBJ_NULL) {
                mp_obj_print(state[i], PRINT_REPR);
            } else {
                mp_printf(&mp_plat_print, "NULL");
            }
            mp_printf(&mp_plat_print, ")\n");
        }
    }

    // Also print all values in the state array for reference
    mp_printf(&mp_plat_print, "DEBUG: Complete state array (n_state=%d):\n", n_state);
    for (uint16_t i = 0; i < n_state; i++) {
        mp_printf(&mp_plat_print, "  state[%d] = ", i);
        if (state[i] != MP_OBJ_NULL) {
            mp_obj_print(state[i], PRINT_REPR);
        } else {
            mp_printf(&mp_plat_print, "NULL");
        }
        mp_printf(&mp_plat_print, "\n");
    }
}
// This is exposed as debug_locals_info() in the sys module
mp_obj_t mp_debug_locals_info(void) {
    mp_code_state_t *code_state = MP_STATE_THREAD(current_code_state);
    if (code_state == NULL || code_state->fun_bc == NULL || code_state->fun_bc->rc == NULL) {
        mp_print_str(&mp_plat_print, "No active code state or function\n");
        return mp_const_none;
    }

    mp_print_str(&mp_plat_print, "\n=== DEBUG LOCALS INFO ===\n");
    mp_printf(&mp_plat_print, "Code state: %p, n_state: %d\n", code_state, code_state->n_state);

    // Print function details
    const mp_raw_code_t *rc = code_state->fun_bc->rc;
    mp_printf(&mp_plat_print, "Function: prelude.n_pos_args=%d, prelude.n_kwonly_args=%d\n",
        rc->prelude.n_pos_args, rc->prelude.n_kwonly_args);

    // Print the mappings and the state values
    mp_debug_print_local_variables(rc, code_state->state, code_state->n_state);

    mp_print_str(&mp_plat_print, "=== END DEBUG INFO ===\n\n");
    return mp_const_none;
}

#endif // MICROPY_PY_SYS_SETTRACE_SAVE_NAMES
