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
#include "py/gc.h"
#include "py/mperrno.h"
#include "shared/runtime/gchelper.h"
#include "shared/runtime/pyexec.h"

#if MICROPY_ZEPHYR_THREADING
#include <zephyr/kernel.h>
#endif

#if MICROPY_HEAP_SIZE <= 0
#error MICROPY_HEAP_SIZE must be a positive integer.
#endif

static uint32_t gc_heap[MICROPY_HEAP_SIZE / sizeof(uint32_t)];

#if MICROPY_ZEPHYR_THREADING
// Zephyr threading entry point - called by Zephyr kernel after z_cstart()
// This function runs in z_main_thread context after kernel initialization
void micropython_main_thread_entry(void *p1, void *p2, void *p3) {
    (void)p1;
    (void)p2;
    (void)p3;

    // NOTE: We're now running in z_main_thread context, not boot/dummy context
    // This means k_thread_create() and other threading operations are safe

    goto micropython_soft_reset;

micropython_soft_reset:
    // Threading early init - Phase 1 (set thread-local state FIRST)
    // Must be called before ANY code that accesses MP_STATE_THREAD()
    // This includes mp_cstack_init_with_top() and gc_init()
    if (!mp_thread_init_early()) {
        mp_printf(&mp_plat_print, "Failed to initialize threading (early phase)\n");
        for (;;) {}
    }

    // Stack limit init
    // After zephyr_psp_init (called from Reset_Handler), main thread runs on
    // z_main_stack (via PSP). Get stack info from z_main_thread which was
    // initialized by prepare_multithreading() in zephyr_cstart.c.
    extern struct k_thread z_main_thread;
    char *stack_top = (char *)z_main_thread.stack_info.start + z_main_thread.stack_info.size;
    size_t stack_size = z_main_thread.stack_info.size;
    mp_cstack_init_with_top(stack_top, stack_size);

    // GC init
    gc_init(gc_heap, (char *)gc_heap + sizeof(gc_heap));

    // Threading init - Phase 2 (allocate main thread on GC heap)
    // Requires GC to be initialized for m_new_obj() heap allocation
    char stack_dummy;
    if (!mp_thread_init(&stack_dummy)) {
        mp_printf(&mp_plat_print, "Failed to initialize threading (phase 2)\n");
        for (;;) {}
    }

    // Enable SysTick interrupt now that threading is fully initialized
    extern void mp_zephyr_arch_enable_systick_interrupt(void);
    mp_zephyr_arch_enable_systick_interrupt();

    // MicroPython init
    mp_init();

    // Run REPL loop
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

    mp_thread_deinit();
    gc_sweep_all();
    mp_deinit();

    goto micropython_soft_reset;
}
#endif // MICROPY_ZEPHYR_THREADING

int main(int argc, char **argv) {
    #if MICROPY_ZEPHYR_THREADING
    // Initialize Zephyr architecture layer (configures SysTick for Zephyr timing)
    extern void mp_zephyr_arch_init(void);
    mp_zephyr_arch_init();

    // Transfer control to Zephyr kernel
    // z_cstart() initializes Zephyr kernel and never returns
    extern void z_cstart(void);
    z_cstart();  // Never returns - Zephyr takes over and calls micropython_main_thread_entry()
    #else
    // Non-threading build: simple main loop
    mp_cstack_init_with_sp_here(10240);
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

        gc_sweep_all();
        mp_deinit();
    }
    #endif

    return 0;
}

void gc_collect(void) {
    gc_collect_start();
    gc_helper_collect_regs_and_stack();
    #if MICROPY_ZEPHYR_THREADING
    mp_thread_gc_others();
    #endif
    gc_collect_end();
}

void nlr_jump_fail(void *val) {
    mp_printf(&mp_plat_print, "uncaught NLR\n");
    exit(1);
}
