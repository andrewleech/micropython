/*
 * This file is part of the MicroPython project, http://micropython.org/
 *
 * The MIT License (MIT)
 *
 * Copyright (c) 2025 Jos Verlinde
 *
 * Permission is hereby granted, free
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
#include "py/localnames.h"

#if MICROPY_PY_SYS_SETTRACE_SAVE_NAMES

// Initialize the local names structure
void mp_local_names_init(mp_local_names_t *local_names) {
    if (local_names == NULL) {
        return;
    }

    local_names->num_locals = 0;
    local_names->order_count = 0;

    // Initialize all entries with null qstrs and invalid indices
    for (uint16_t i = 0; i < MICROPY_PY_SYS_SETTRACE_NAMES_MAX; i++) {
        local_names->local_names[i] = MP_QSTRnull;
        local_names->local_nums[i] = UINT16_MAX; // Invalid index marker
    }
}

// Get the name of a local variable by its index
qstr mp_local_names_get_name(const mp_local_names_t *local_names, uint16_t local_num) {
    if (local_names == NULL || local_num >= MICROPY_PY_SYS_SETTRACE_NAMES_MAX) {
        return MP_QSTRnull;
    }

    // Direct array access
    return local_names->local_names[local_num];
}

// Look up the original local_num by order index (source code order)
uint16_t mp_local_names_get_local_num(const mp_local_names_t *local_names, uint16_t order_idx) {
    if (local_names == NULL || order_idx >= local_names->order_count) {
        return UINT16_MAX; // Invalid index
    }

    return local_names->local_nums[order_idx];
}

// Add or update a name mapping for a local variable
void mp_local_names_add(mp_local_names_t *local_names, uint16_t local_num, qstr qstr_name) {
    if (local_names == NULL || local_num >= MICROPY_PY_SYS_SETTRACE_NAMES_MAX) {
        return;
    }

    // Store name directly using local_num as index
    local_names->local_names[local_num] = qstr_name;

    // Update number of locals if needed
    if (local_num >= local_names->num_locals) {
        local_names->num_locals = local_num + 1;
    }

    // Also store in order of definition for correct runtime mapping
    if (local_names->order_count < MICROPY_PY_SYS_SETTRACE_NAMES_MAX) {
        uint16_t idx = local_names->order_count;
        local_names->local_nums[idx] = local_num;
        local_names->order_count++;
    }

    // Refine runtime slot mapping logic
    // Test the hypothesis that variables are assigned from highest slots down
    uint16_t runtime_slot = local_num; // Default to direct mapping

    if (local_names->order_count > 0) {
        // Find position in order array
        for (uint16_t i = 0; i < local_names->order_count; ++i) {
            if (local_names->local_nums[i] == local_num) {
                runtime_slot = i;
                break;
            }
        }
    }
    local_names->runtime_slots[local_num] = runtime_slot;
}

// Get the runtime slot for a local variable by its index
uint16_t mp_local_names_get_runtime_slot(const mp_local_names_t *local_names, uint16_t local_num) {
    if (local_names == NULL || local_num >= MICROPY_PY_SYS_SETTRACE_NAMES_MAX) {
        return UINT16_MAX; // Invalid slot
    }
    return local_names->runtime_slots[local_num];
}

#endif // MICROPY_PY_SYS_SETTRACE_SAVE_NAMES
