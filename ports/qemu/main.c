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

#if MICROPY_PY_THREAD
#include "py/mpthread.h"
#include "FreeRTOS.h"
#include "task.h"
#endif

#if MICROPY_HEAP_SIZE <= 0
#error MICROPY_HEAP_SIZE must be a positive integer.
#endif

static uint32_t gc_heap[MICROPY_HEAP_SIZE / sizeof(uint32_t)];

#if MICROPY_PY_THREAD
// Main task stack and TCB for static allocation
#define MAIN_TASK_STACK_SIZE (4096 / sizeof(StackType_t))
static StaticTask_t main_task_tcb;
static StackType_t main_task_stack[MAIN_TASK_STACK_SIZE];
#endif

// Forward declaration
static void qemu_main_loop(void *arg);

int main(int argc, char **argv) {
    #if MICROPY_PY_THREAD
    // With threading, all initialization happens inside the FreeRTOS task
    // because mp_cstack_init_with_sp_here and gc_init use MP_STATE_THREAD
    // which requires FreeRTOS thread-local storage (needs running task context)

    // Create main task using static allocation
    xTaskCreateStatic(
        qemu_main_loop,
        "main",
        MAIN_TASK_STACK_SIZE,
        NULL,
        tskIDLE_PRIORITY + 1,
        main_task_stack,
        &main_task_tcb
        );

    // Start the FreeRTOS scheduler - does not return
    vTaskStartScheduler();

    // Should never reach here
    for (;;) {
    }
    #else
    // Non-threaded: initialize here and run directly
    mp_cstack_init_with_sp_here(10240);
    gc_init(gc_heap, (char *)gc_heap + MICROPY_HEAP_SIZE);
    qemu_main_loop(NULL);
    #endif

    return 0;
}

static void qemu_main_loop(void *arg) {
    (void)arg;

    #if MICROPY_PY_THREAD
    // Initialize threading FIRST - sets up TLS which is needed by MP_STATE_THREAD
    mp_thread_init(main_task_stack, sizeof(main_task_stack));
    // Now initialize MicroPython state (these use MP_STATE_THREAD)
    mp_cstack_init_with_sp_here(MAIN_TASK_STACK_SIZE * sizeof(StackType_t));
    gc_init(gc_heap, (char *)gc_heap + MICROPY_HEAP_SIZE);
    #endif

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
    #if MICROPY_PY_THREAD
    mp_thread_gc_others();
    #endif
    gc_collect_end();
}

void nlr_jump_fail(void *val) {
    mp_printf(&mp_plat_print, "uncaught NLR\n");
    exit(1);
}
