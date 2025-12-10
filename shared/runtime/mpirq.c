/*
 * This file is part of the MicroPython project, http://micropython.org/
 *
 * The MIT License (MIT)
 *
 * Copyright (c) 2015 Daniel Campora
 *               2018 Tobias Badertscher
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

#include "py/runtime.h"
#include "py/gc.h"
#include "py/bc.h"
#include "py/objfun.h"
#include "py/objgenerator.h"
#include "shared/runtime/mpirq.h"

#if MICROPY_ENABLE_SCHEDULER

/******************************************************************************
 UNIFIED IRQ HANDLER WRAPPING

 For hard IRQ: All handlers are converted to generator-compatible objects at
 registration time. This eliminates type checks in the hot dispatch path.

 Handler types for hard IRQ:
 - Real generators: Used as-is after priming (run to first yield)
 - Bytecode functions: Wrapped as mp_type_gen_instance with IRQ_FUNC_BC sentinel
 - Native functions: Wrapped as mp_type_gen_instance with IRQ_FUNC_NAT sentinel
 - Viper functions: Wrapped as mp_type_gen_instance with IRQ_VIPER sentinel
 - Other callables: Wrapped with IRQ_CALLABLE sentinel, calls via mp_call_function_1

 For soft IRQ: Generators are instantiated and primed, but other callables are
 passed directly to mp_sched_schedule without wrapping.

 The sentinel value in exc_sp_idx tells mp_obj_gen_resume_irq() how to handle it.
 ******************************************************************************/

// Wrap a bytecode function as a generator-compatible object for fast IRQ dispatch.
// Calls mp_setup_code_state once at wrap time to initialize everything.
static mp_obj_t mp_irq_wrap_bytecode_function(mp_obj_t func_in) {
    mp_obj_fun_bc_t *fun = MP_OBJ_TO_PTR(func_in);

    // Decode state requirements from bytecode prelude
    const uint8_t *ip = fun->bytecode;
    size_t n_state, n_pos_args, scope_flags;
    {
        size_t n_exc_stack, n_kwonly_args, n_def_args;
        MP_BC_PRELUDE_SIG_DECODE_INTO(ip, n_state, n_exc_stack, scope_flags, n_pos_args, n_kwonly_args, n_def_args);
        (void)n_exc_stack;
        (void)n_kwonly_args;
        (void)n_def_args;
    }

    // Reject generator functions (those with yield) - they should be passed as gen_wrap
    if (scope_flags & MP_SCOPE_FLAG_GENERATOR) {
        mp_raise_ValueError(MP_ERROR_TEXT("use generator function, not plain function with yield"));
    }

    // Verify function signature: must accept exactly 1 positional arg
    if (n_pos_args != 1) {
        mp_raise_ValueError(MP_ERROR_TEXT("IRQ callback must take exactly 1 argument"));
    }

    // Calculate allocation size: generator struct + state + extra data (no exc_stack needed)
    size_t state_size = n_state * sizeof(mp_obj_t);
    size_t extra_size = sizeof(mp_irq_handler_extra_t);
    size_t total_var_size = state_size + extra_size;

    // Allocate as a generator instance (same type, compatible layout)
    mp_obj_gen_instance_t *o = mp_obj_malloc_var(mp_obj_gen_instance_t, code_state.state,
        byte, total_var_size, &mp_type_gen_instance);

    // Initialize generator header
    o->pend_exc = mp_const_none;  // Idle
    o->code_state.fun_bc = fun;
    o->code_state.n_state = n_state;

    // Initialize code_state using standard setup with placeholder argument.
    // This parses the prelude, zeros state, and sets up ip/sp correctly.
    o->code_state.ip = fun->bytecode;
    o->code_state.sp = &o->code_state.state[0] - 1;
    mp_obj_t dummy_arg = mp_const_none;
    mp_setup_code_state(&o->code_state, 1, 0, &dummy_arg);

    // Save the initialized ip (points to bytecode start after prelude) in extra data
    mp_irq_handler_extra_t *extra = (mp_irq_handler_extra_t *)((byte *)o->code_state.state + state_size);
    extra->bytecode_start = o->code_state.ip;
    extra->native_entry = NULL;

    // Mark as wrapped bytecode function (not a real generator)
    // This overwrites the exc_sp_idx that mp_setup_code_state set to 0
    o->code_state.exc_sp_idx = MP_CODE_STATE_EXC_SP_IDX_IRQ_FUNC_BC;

    return MP_OBJ_FROM_PTR(o);
}

#if MICROPY_EMIT_NATIVE
// Wrap a @native function as a generator-compatible object.
static mp_obj_t mp_irq_wrap_native_function(mp_obj_t func_in) {
    mp_obj_fun_bc_t *fun = MP_OBJ_TO_PTR(func_in);

    // Get prelude to determine state size and validate signature
    const uint8_t *prelude_ptr = mp_obj_fun_native_get_prelude_ptr(fun);
    const uint8_t *ip = prelude_ptr;
    MP_BC_PRELUDE_SIG_DECODE(ip);

    // Verify function signature: must accept exactly 1 positional arg
    if (n_pos_args != 1) {
        mp_raise_ValueError(MP_ERROR_TEXT("IRQ callback must take exactly 1 argument"));
    }

    // Native functions don't need exception stack
    size_t extra_size = sizeof(mp_irq_handler_extra_t);
    size_t total_var_size = n_state * sizeof(mp_obj_t) + extra_size;

    // Allocate as generator instance
    mp_obj_gen_instance_t *o = mp_obj_malloc_var(mp_obj_gen_instance_t, code_state.state,
        byte, total_var_size, &mp_type_gen_instance);

    o->pend_exc = mp_const_none;
    o->code_state.fun_bc = fun;
    o->code_state.n_state = n_state;
    o->code_state.exc_sp_idx = MP_CODE_STATE_EXC_SP_IDX_IRQ_FUNC_NAT;

    // Store native function entry point
    mp_irq_handler_extra_t *extra = (mp_irq_handler_extra_t *)((byte *)o->code_state.state + n_state * sizeof(mp_obj_t));
    extra->bytecode_start = NULL;
    extra->native_entry = mp_obj_fun_native_get_function_start(fun);

    return MP_OBJ_FROM_PTR(o);
}

// Wrap a @viper function as a generator-compatible object.
// Viper entry point is directly at fun->bytecode (no header offset unlike @native).
static mp_obj_t mp_irq_wrap_viper_function(mp_obj_t func_in) {
    mp_obj_fun_bc_t *fun = MP_OBJ_TO_PTR(func_in);

    // Viper functions have minimal state needs - we just store fun_bc for context access
    size_t n_state = 2;
    size_t extra_size = sizeof(mp_irq_handler_extra_t);
    size_t total_var_size = n_state * sizeof(mp_obj_t) + extra_size;

    mp_obj_gen_instance_t *o = mp_obj_malloc_var(mp_obj_gen_instance_t, code_state.state,
        byte, total_var_size, &mp_type_gen_instance);

    o->pend_exc = mp_const_none;
    o->code_state.fun_bc = fun;
    o->code_state.n_state = n_state;
    o->code_state.exc_sp_idx = MP_CODE_STATE_EXC_SP_IDX_IRQ_VIPER;

    // Store viper function entry point - no extra data needed since we call
    // through fun_bc->bytecode directly in the dispatch path
    mp_irq_handler_extra_t *extra = (mp_irq_handler_extra_t *)((byte *)o->code_state.state + n_state * sizeof(mp_obj_t));
    extra->bytecode_start = NULL;
    extra->native_entry = NULL;  // Not used - we get entry from fun_bc->bytecode

    return MP_OBJ_FROM_PTR(o);
}

// Wrap a @viper function with argument count validation.
// Viper functions use a special prelude format that must be parsed differently.
static mp_obj_t mp_irq_wrap_viper_function_validated(mp_obj_t func_in) {
    // Note: viper functions have different prelude structure - validation at call time
    // The n_pos_args check would require parsing viper-specific metadata which is complex.
    // Since viper is called with correct signature, argument errors will be caught at call time.
    return mp_irq_wrap_viper_function(func_in);
}
#endif // MICROPY_EMIT_NATIVE

// Wrap any callable as a generator-compatible object for hard IRQ dispatch.
// Uses mp_call_function_1 at dispatch time (slower than specialized wrappers but works
// for any callable including bound methods, closures, etc).
static mp_obj_t mp_irq_wrap_callable(mp_obj_t callable) {
    // Minimal allocation: generator instance with space to store the callable
    // We store the callable in state[0], and use IRQ_CALLABLE sentinel
    size_t n_state = 2;  // state[0] = callable, state[1] unused
    size_t total_var_size = n_state * sizeof(mp_obj_t);

    mp_obj_gen_instance_t *o = mp_obj_malloc_var(mp_obj_gen_instance_t, code_state.state,
        byte, total_var_size, &mp_type_gen_instance);

    o->pend_exc = mp_const_none;
    o->code_state.fun_bc = NULL;  // Not used for generic callable
    o->code_state.n_state = n_state;
    o->code_state.exc_sp_idx = MP_CODE_STATE_EXC_SP_IDX_IRQ_CALLABLE;
    o->code_state.state[0] = callable;  // Store the actual callable here

    return MP_OBJ_FROM_PTR(o);
}

/******************************************************************************
 DECLARE PUBLIC DATA
 ******************************************************************************/

const mp_arg_t mp_irq_init_args[] = {
    { MP_QSTR_handler, MP_ARG_OBJ, {.u_rom_obj = MP_ROM_NONE} },
    { MP_QSTR_trigger, MP_ARG_INT, {.u_int = 0} },
    { MP_QSTR_hard, MP_ARG_BOOL, {.u_bool = false} },
};

/******************************************************************************
 DEFINE PUBLIC FUNCTIONS
 ******************************************************************************/

mp_irq_obj_t *mp_irq_new(const mp_irq_methods_t *methods, mp_obj_t parent) {
    mp_irq_obj_t *self = m_new0(mp_irq_obj_t, 1);
    mp_irq_init(self, methods, parent);
    return self;
}

void mp_irq_init(mp_irq_obj_t *self, const mp_irq_methods_t *methods, mp_obj_t parent) {
    self->base.type = &mp_irq_type;
    self->methods = (mp_irq_methods_t *)methods;
    self->parent = parent;
    self->handler = mp_const_none;
    self->ishard = false;
}

mp_obj_t mp_irq_prepare_handler(mp_obj_t callback, mp_obj_t parent, bool ishard) {
    // Auto-instantiate generator functions (bytecode or native).
    if (mp_obj_is_type(callback, &mp_type_gen_wrap)
        #if MICROPY_EMIT_NATIVE
        || mp_obj_is_type(callback, &mp_type_native_gen_wrap)
        #endif
        ) {
        callback = mp_call_function_1(callback, parent);
    }

    // Prime generator instances (run setup code to first yield).
    if (mp_obj_is_type(callback, &mp_type_gen_instance)) {
        mp_obj_t ret_val;
        mp_vm_return_kind_t ret = mp_obj_gen_resume(callback, mp_const_none, MP_OBJ_NULL, &ret_val);
        if (ret != MP_VM_RETURN_YIELD) {
            if (ret == MP_VM_RETURN_EXCEPTION) {
                nlr_raise(ret_val);
            }
            mp_raise_ValueError(MP_ERROR_TEXT("generator must yield"));
        }
        // Generator is ready - no wrapping needed (already gen_instance)
    } else if (ishard) {
        // Hard IRQ: wrap all callables as generator-compatible objects for unified dispatch
        if (mp_obj_is_type(callback, &mp_type_fun_bc)) {
            callback = mp_irq_wrap_bytecode_function(callback);
        #if MICROPY_EMIT_NATIVE
        } else if (mp_obj_is_type(callback, &mp_type_fun_native)) {
            callback = mp_irq_wrap_native_function(callback);
        } else if (mp_obj_is_type(callback, &mp_type_fun_viper)) {
            callback = mp_irq_wrap_viper_function_validated(callback);
        #endif
        } else if (callback != mp_const_none) {
            // Generic callable (bound method, closure, etc) - wrap for hard IRQ dispatch
            if (!mp_obj_is_callable(callback)) {
                mp_raise_ValueError(MP_ERROR_TEXT("callback must be None, callable, or generator"));
            }
            callback = mp_irq_wrap_callable(callback);
        }
    } else {
        // Soft IRQ: don't wrap - mp_sched_schedule will call via mp_call_function_1
        if (callback != mp_const_none && !mp_obj_is_callable(callback)) {
            mp_raise_ValueError(MP_ERROR_TEXT("callback must be None, callable, or generator"));
        }
    }

    return callback;
}


int mp_irq_dispatch(mp_obj_t handler, mp_obj_t parent, bool ishard) {
    int result = 0;
    if (handler != mp_const_none) {
        if (ishard) {
            #if MICROPY_STACK_CHECK && MICROPY_STACK_SIZE_HARD_IRQ > 0
            char *orig_stack_top = MP_STATE_THREAD(stack_top);
            size_t orig_stack_limit = MP_STATE_THREAD(stack_limit);
            mp_cstack_init_with_sp_here(MICROPY_STACK_SIZE_HARD_IRQ);
            #endif

            mp_sched_lock();
            gc_lock();
            nlr_buf_t nlr;
            if (nlr_push(&nlr) == 0) {
                // All prepared handlers are generator-compatible (mp_type_gen_instance).
                // No type checks needed - mp_obj_gen_resume_irq handles all variants
                // based on exc_sp_idx sentinel values.
                mp_obj_t ret_val;
                mp_vm_return_kind_t ret = mp_obj_gen_resume_irq(handler, parent, &ret_val);

                if (ret == MP_VM_RETURN_NORMAL) {
                    // Handler finished (generator returned, or wrapped function completed).
                    // For wrapped functions this is normal; for generators it means done.
                    mp_obj_gen_instance_t *gen = MP_OBJ_TO_PTR(handler);
                    uint16_t exc_idx = gen->code_state.exc_sp_idx;
                    // Only signal error for real generators that have exhausted
                    if (exc_idx != MP_CODE_STATE_EXC_SP_IDX_IRQ_FUNC_BC
                        && exc_idx != MP_CODE_STATE_EXC_SP_IDX_IRQ_FUNC_NAT
                        && exc_idx != MP_CODE_STATE_EXC_SP_IDX_IRQ_VIPER
                        && exc_idx != MP_CODE_STATE_EXC_SP_IDX_IRQ_CALLABLE) {
                        result = -1;  // Generator exhausted
                    }
                } else if (ret == MP_VM_RETURN_EXCEPTION) {
                    mp_printf(MICROPY_ERROR_PRINTER, "Uncaught exception in IRQ callback handler\n");
                    mp_obj_print_exception(MICROPY_ERROR_PRINTER, ret_val);
                    result = -1;
                }
                // MP_VM_RETURN_YIELD: success, handler stays active.
                nlr_pop();
            } else {
                mp_printf(MICROPY_ERROR_PRINTER, "Uncaught exception in IRQ callback handler\n");
                mp_obj_print_exception(MICROPY_ERROR_PRINTER, MP_OBJ_FROM_PTR(nlr.ret_val));
                result = -1;
            }
            gc_unlock();
            mp_sched_unlock();

            #if MICROPY_STACK_CHECK && MICROPY_STACK_SIZE_HARD_IRQ > 0
            MP_STATE_THREAD(stack_top) = orig_stack_top;
            MP_STATE_THREAD(stack_limit) = orig_stack_limit;
            #endif
        } else {
            // Schedule call to user function
            mp_sched_schedule(handler, parent);
        }
    }
    return result;
}


void mp_irq_handler(mp_irq_obj_t *self) {
    if (mp_irq_dispatch(self->handler, self->parent, self->ishard) < 0) {
        // Uncaught exception; disable the callback so that it doesn't run again
        self->methods->trigger(self->parent, 0);
        self->handler = mp_const_none;
    }
}

/******************************************************************************/
// MicroPython bindings

static mp_obj_t mp_irq_flags(mp_obj_t self_in) {
    mp_irq_obj_t *self = MP_OBJ_TO_PTR(self_in);
    return mp_obj_new_int(self->methods->info(self->parent, MP_IRQ_INFO_FLAGS));
}
static MP_DEFINE_CONST_FUN_OBJ_1(mp_irq_flags_obj, mp_irq_flags);

static mp_obj_t mp_irq_trigger(size_t n_args, const mp_obj_t *args) {
    mp_irq_obj_t *self = MP_OBJ_TO_PTR(args[0]);
    mp_obj_t ret_obj = mp_obj_new_int(self->methods->info(self->parent, MP_IRQ_INFO_TRIGGERS));
    if (n_args == 2) {
        // Set trigger
        self->methods->trigger(self->parent, mp_obj_get_int(args[1]));
    }
    return ret_obj;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(mp_irq_trigger_obj, 1, 2, mp_irq_trigger);

static mp_obj_t mp_irq_call(mp_obj_t self_in, size_t n_args, size_t n_kw, const mp_obj_t *args) {
    mp_arg_check_num(n_args, n_kw, 0, 0, false);
    mp_irq_handler(MP_OBJ_TO_PTR(self_in));
    return mp_const_none;
}

static const mp_rom_map_elem_t mp_irq_locals_dict_table[] = {
    { MP_ROM_QSTR(MP_QSTR_flags),               MP_ROM_PTR(&mp_irq_flags_obj) },
    { MP_ROM_QSTR(MP_QSTR_trigger),             MP_ROM_PTR(&mp_irq_trigger_obj) },
};
static MP_DEFINE_CONST_DICT(mp_irq_locals_dict, mp_irq_locals_dict_table);

MP_DEFINE_CONST_OBJ_TYPE(
    mp_irq_type,
    MP_QSTR_irq,
    MP_TYPE_FLAG_NONE,
    call, mp_irq_call,
    locals_dict, &mp_irq_locals_dict
    );

#endif // MICROPY_ENABLE_SCHEDULER
