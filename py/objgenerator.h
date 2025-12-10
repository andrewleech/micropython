/*
 * This file is part of the MicroPython project, http://micropython.org/
 *
 * The MIT License (MIT)
 *
 * Copyright (c) 2013, 2014 Damien P. George
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
#ifndef MICROPY_INCLUDED_PY_OBJGENERATOR_H
#define MICROPY_INCLUDED_PY_OBJGENERATOR_H

#include "py/obj.h"
#include "py/runtime.h"
#include "py/bc.h"

// Generator instance structure (also used for wrapped IRQ handlers)
typedef struct _mp_obj_gen_instance_t {
    mp_obj_base_t base;
    // mp_const_none: Not-running, no exception.
    // MP_OBJ_NULL: Running, no exception.
    // other: Not running, pending exception.
    mp_obj_t pend_exc;
    mp_code_state_t code_state;
} mp_obj_gen_instance_t;

mp_vm_return_kind_t mp_obj_gen_resume(mp_obj_t self_in, mp_obj_t send_val, mp_obj_t throw_val, mp_obj_t *ret_val);

#if MICROPY_ENABLE_SCHEDULER
// Fast path for IRQ handlers - skips primed check since IRQ generators are always primed.
mp_vm_return_kind_t mp_obj_gen_resume_irq(mp_obj_t self_in, mp_obj_t send_val, mp_obj_t *ret_val);
#endif

#endif // MICROPY_INCLUDED_PY_OBJGENERATOR_H
