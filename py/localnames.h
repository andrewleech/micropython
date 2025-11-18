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

#ifndef MICROPY_INCLUDED_PY_LOCALNAMES_H
#define MICROPY_INCLUDED_PY_LOCALNAMES_H

#include "py/obj.h"
#include "py/qstr.h"

#if MICROPY_PY_SYS_SETTRACE_SAVE_NAMES

#define MICROPY_PY_SYS_SETTRACE_NAMES_MAX 32  // Maximum number of local variables to store names for

// Structure to hold variable name mappings for a function scope
typedef struct _mp_local_names_t {
    uint16_t num_locals;                 // Total number of local variables with names
    qstr local_names[MICROPY_PY_SYS_SETTRACE_NAMES_MAX];  // Array of variable names, indexed by local_num
    uint16_t local_nums[MICROPY_PY_SYS_SETTRACE_NAMES_MAX]; // Reverse mapping: name index -> local_num (for correct state array mapping)
    uint16_t order_count;                 // Number of variables stored in order they were defined
    uint16_t runtime_slots[MICROPY_PY_SYS_SETTRACE_NAMES_MAX]; // Mapping of local_num to runtime slots
} mp_local_names_t;

// Initialize the local names structure
void mp_local_names_init(mp_local_names_t *local_names);

// Function to look up a variable name by its index
qstr mp_local_names_get_name(const mp_local_names_t *local_names, uint16_t local_num);

// Function to look up the original local_num by order index (source code order)
uint16_t mp_local_names_get_local_num(const mp_local_names_t *local_names, uint16_t order_idx);

// Function to add or update a name mapping for a local variable
void mp_local_names_add(mp_local_names_t *local_names, uint16_t local_num, qstr qstr_name);

// Function to get the runtime slot of a local variable by its index
uint16_t mp_local_names_get_runtime_slot(const mp_local_names_t *local_names, uint16_t local_num);

#endif // MICROPY_PY_SYS_SETTRACE_SAVE_NAMES

#endif // MICROPY_INCLUDED_PY_LOCALNAMES_H
