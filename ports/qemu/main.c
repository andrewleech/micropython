/*
 * This file is part of the MicroPython project, http://micropython.org/
 *
 * The MIT License (MIT)
 *
 * Copyright (c) 2014-2023 Damien P. George
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

#include <stdlib.h>

#include "py/compile.h"
#include "py/runtime.h"
#include "py/stackctrl.h"
#include "py/gc.h"
#include "py/mperrno.h"
#include "shared/runtime/gchelper.h"
#include "shared/runtime/pyexec.h"

#if MICROPY_PY_THREAD
#include "py/mpthread.h"
#endif

#if MICROPY_ZEPHYR_THREADING
#include <zephyr/kernel.h>
#endif

#if MICROPY_HEAP_SIZE <= 0
#error MICROPY_HEAP_SIZE must be a positive integer.
#endif

static uint32_t gc_heap[MICROPY_HEAP_SIZE / sizeof(uint32_t)];

#if !MICROPY_ZEPHYR_THREADING
// When Zephyr threading is disabled, use traditional main() entry point
int main(int argc, char **argv) {
    (void)argc;
    (void)argv;
#else
// When Zephyr threading is enabled, this function runs IN z_main_thread context
// It's called by z_cstart() after context switching from boot/dummy thread
void micropython_main_thread_entry(void *p1, void *p2, void *p3) {
    (void)p1;
    (void)p2;
    (void)p3;

    // Enable SysTick interrupt now that kernel is fully initialized
    // (SysTick counter was started in mp_zephyr_arch_init() but interrupt was disabled)
    extern void mp_zephyr_arch_enable_systick_interrupt(void);
    mp_zephyr_arch_enable_systick_interrupt();

    // NOTE: We're now running in z_main_thread context, not boot/dummy context
    // This means k_thread_create() and other threading operations are safe to call
#endif

    // Initialize MicroPython threading
    #if MICROPY_PY_THREAD
    #if MICROPY_ZEPHYR_THREADING
    // For Zephyr threading, pass stack pointer for thread-local storage
    char stack_dummy;
    if (!mp_thread_init(&stack_dummy)) {
        mp_printf(&mp_plat_print, "Failed to initialize threading\n");
        #if !MICROPY_ZEPHYR_THREADING
        return 1;
        #else
        // In thread context, can't return - just loop forever
        for (;;) {}
        #endif
    }
    #else
    mp_thread_init(NULL, 0);
    #endif
    #endif

    // Configure stack
    mp_stack_ctrl_init();
    mp_stack_set_limit(10240);

    // Initialize garbage collector
    gc_init(gc_heap, (char *)gc_heap + MICROPY_HEAP_SIZE);

    for (;;) {
        mp_init();

        for (;;) {
            if (pyexec_mode_kind == PYEXEC_MODE_RAW_REPL) {
                if (pyexec_raw_repl() != 0) {
                    break;
                }
            } else {
                if (pyexec_friendly_repl() != 0) {
                    break;
                }
            }
        }

        mp_printf(&mp_plat_print, "MPY: soft reboot\n");

        #if MICROPY_PY_THREAD
        mp_thread_deinit();
        #endif

        gc_sweep_all();
        mp_deinit();
    }
}

void gc_collect(void) {
    gc_collect_start();
    gc_helper_collect_regs_and_stack();
    gc_collect_end();
}

void nlr_jump_fail(void *val) {
    mp_printf(&mp_plat_print, "uncaught NLR\n");
    exit(1);
}
