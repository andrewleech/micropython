/*
 * This file is part of the MicroPython project, http://micropython.org/
 *
 * The MIT License (MIT)
 *
 * Copyright (c) 2025 Contributors to the MicroPython project
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

#if MICROPY_SAVE_LOCAL_VARIABLE_NAMES

// Initialize the local names structure
void mp_local_names_init(mp_local_names_t *local_names) {
    if (local_names == NULL) {
        return;
    }
    
    local_names->num_locals = 0;
    local_names->order_count = 0;
    
    // Initialize all entries with null qstrs and invalid indices
    for (uint16_t i = 0; i < MP_LOCAL_NAMES_MAX; i++) {
        local_names->local_names[i] = MP_QSTRnull;
        local_names->local_nums[i] = UINT16_MAX; // Invalid index marker
    }
}

// Get the name of a local variable by its index
qstr mp_local_names_get_name(const mp_local_names_t *local_names, uint16_t local_num) {
    if (local_names == NULL || local_num >= MP_LOCAL_NAMES_MAX) {
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
    if (local_names == NULL || local_num >= MP_LOCAL_NAMES_MAX) {
        return;
    }

    // Debug the mapping
    mp_printf(&mp_plat_print, "DEBUG_ADD_NAME: Adding local name mapping: slot %d -> '%s'\n", 
             local_num, qstr_str(qstr_name));

    // Store name directly using local_num as index
    local_names->local_names[local_num] = qstr_name;

    // Update number of locals if needed
    if (local_num >= local_names->num_locals) {
        local_names->num_locals = local_num + 1;
    }

    // Also store in order of definition for correct runtime mapping
    if (local_names->order_count < MP_LOCAL_NAMES_MAX) {
        uint16_t idx = local_names->order_count;
        local_names->local_nums[idx] = local_num;
        local_names->order_count++;

        // Debug the order mapping
        mp_printf(&mp_plat_print, "DEBUG_ORDER_MAP: Source order idx %d -> runtime slot %d\n", 
                 idx, local_num);
    }

    // Enhance debug output to capture runtime behavior
    mp_printf(&mp_plat_print, "DEBUG_RUNTIME_SLOT: Local %d ('%s') -> Runtime Slot calculation\n", 
              local_num, qstr_str(qstr_name));

    // Refine runtime slot mapping logic
    // Test the hypothesis that variables are assigned from highest slots down
    uint16_t runtime_slot = local_num; // Default to direct mapping
    
    if (local_names->order_count > 0) {
        // Find position in order array
        for (uint16_t i = 0; i < local_names->order_count; ++i) {
            if (local_names->local_nums[i] == local_num) {
                // Try different slot assignment strategies
                
                // Strategy 1: Sequential after parameters (traditional)
                runtime_slot = i;
                
                // Strategy 2: Reverse order assignment (testing hypothesis)
                // This would assign first variable to highest available slot
                // uint16_t reverse_slot = MP_LOCAL_NAMES_MAX - 1 - i;
                // runtime_slot = reverse_slot;
                
                mp_printf(&mp_plat_print, "DEBUG_RUNTIME_SLOT: Variable '%s' order_idx=%d -> runtime_slot=%d\n",
                          qstr_str(qstr_name), i, runtime_slot);
                break;
            }
        }
    }
    local_names->runtime_slots[local_num] = runtime_slot;
}

// Get the runtime slot for a local variable by its index
uint16_t mp_local_names_get_runtime_slot(const mp_local_names_t *local_names, uint16_t local_num) {
    if (local_names == NULL || local_num >= MP_LOCAL_NAMES_MAX) {
        return UINT16_MAX; // Invalid slot
    }
    return local_names->runtime_slots[local_num];
}

#endif // MICROPY_SAVE_LOCAL_VARIABLE_NAMES
