/*
 * This file is part of the MicroPython project, http://micropython.org/
 *
 * The MIT License (MIT)
 *
 * Copyright (c) 2026 MicroPython contributors
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

#include <string.h>

#include "py/builtin.h"
#include "py/compile.h"
#include "py/gc.h"
#include "py/lexer.h"
#include "py/mperrno.h"
#include "py/mphal.h"
#include "py/runtime.h"
#include "py/stackctrl.h"
#include "shared/runtime/gchelper.h"
#include "shared/runtime/pyexec.h"

#if MICROPY_HW_ENABLE_USBDEV
#include "usb/dcd_baochip.h"
#endif

#include "hardware/irq.h"
#include "hardware/uart.h"

#include "machine_pin.h"
#include "mphalport.h"

#if MICROPY_HW_ENABLE_USBDEV
#include "shared/tinyusb/mp_usbd.h"
#endif

// Heap and stack bounds defined by the linker script.  _stack_size is
// the stack reservation size (an absolute value, not an address), so it
// has to be referenced via &_stack_size and cast through uintptr_t.
extern uint32_t _heap_start;
extern uint32_t _heap_end;
extern uint32_t _stack_top;
extern uint32_t _stack_size;

// Stack guard size matches the linker's reservation minus a small
// safety margin so MP_STACK_CHECK() trips before we hit the heap.
#define STACK_GUARD_BYTES       (1024)

#if MICROPY_DEBUG_VERBOSE
static void trace(const char *msg) {
    uart_write(MICROPY_HW_UART_REPL, (const uint8_t *)msg, strlen(msg));
}
#else
#define trace(msg) ((void)0)
#endif

int main(void) {
    mp_hal_uart_repl_init();
    trace("TRACE:uart_ok\r\n");

    mp_hal_ticktimer_init();
    trace("TRACE:tick_ok\r\n");

    // MicroPython stack bounds; the heap is set up per soft-reset
    // iteration so Ctrl-D properly clears all dynamic state.
    mp_stack_set_top(&_stack_top);
    mp_stack_set_limit((size_t)(uintptr_t)&_stack_size - STACK_GUARD_BYTES);

    // Reset the interrupt controller exactly ONCE, before the soft-reset
    // loop.  irq_init() clears the whole handler table and disables every
    // MIM line, so it must NOT run per-iteration: the USB DCD interrupt is
    // registered once (the first mp_usbd_init() -> dcd_init() below) and
    // tusb_init() is idempotent thereafter, so a per-iteration irq_init()
    // would silently strip the USB IRQ and leave native USB-CDC input dead
    // after the first soft reset.  Per-iteration handler (re)registration
    // happens inside the loop via mp_hal_stdin_uart_irq_init(), which no
    // longer resets the controller.
    irq_init();
    #if MICROPY_HW_ENABLE_USBDEV
    // Register the UDC IRQ vector once, before the soft-reset loop.
    // irq_init() cleared the handler table; the DCD itself no longer
    // registers the vector so we do it here explicitly.
    dcd_baochip_irq_register();
    #endif

    // Outer loop: each iteration is one full MicroPython session.
    // pyexec_friendly_repl returns non-zero on Ctrl-D, at which point
    // we tear down, re-init, and re-enter -- matching the soft-reset
    // semantics every other MicroPython port provides.
    for (;;) {
        // Per-soft-reset hardware teardown.  Currently only the pin OD
        // emulation state needs clearing; as drivers land (I2C, SPI, PWM,
        // ...), their deinit hooks belong here.
        memset(machine_pin_open_drain_mask, 0, sizeof(machine_pin_open_drain_mask));

        trace("TRACE:gc_init\r\n");
        gc_init(&_heap_start, &_heap_end);
        trace("TRACE:mp_init\r\n");
        mp_init();
        trace("TRACE:uart_irq\r\n");

        // UART RX IRQ feeds stdin_ringbuf; the handler may call
        // mp_sched_keyboard_interrupt() on a Ctrl-C byte, which writes
        // into MicroPython scheduler state, so this must run AFTER
        // mp_init() has initialised that state.  Re-registering the UART
        // handler each iteration is harmless and idempotent -- the
        // controller itself was reset once before the loop, so this only
        // (re)sets the UART handler slot and re-enables its MIM line; the
        // USB DCD handler slot is left untouched and survives soft reset.
        mp_hal_stdin_uart_irq_init();
        trace("TRACE:repl\r\n");

        #if MICROPY_HW_ENABLE_USBDEV
        // TinyUSB stack init.  tusb_init() is idempotent, so on the first
        // iteration this brings up the DCD (and registers + enables the
        // USB interrupt); on later iterations it is a no-op and the
        // already-enumerated device persists across the soft reset.
        mp_usbd_init();
        // Re-arm the UDC interrupt for this session.  It was masked
        // (dcd_baochip_irq_pause) before the previous mp_deinit() so a
        // USB event couldn't run the DCD ISR -> mp_sched_schedule_node
        // against a half-rebuilt runtime.  Idempotent on the first
        // iteration (dcd_init already enabled it).
        dcd_baochip_irq_resume();
        #endif

        mp_printf(MP_PYTHON_PRINTER, "\nMicroPython baochip port\n");

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

        mp_printf(MP_PYTHON_PRINTER, "MPY: soft reboot\n");
        // Quiesce the interrupt sources whose handlers touch MicroPython
        // scheduler state before tearing the runtime down.  The UART RX
        // handler calls mp_sched_keyboard_interrupt(); the UDC handler
        // schedules the TinyUSB task.  Both would corrupt a half-torn-down
        // runtime if they fired during mp_deinit().  The UART line is
        // re-enabled by mp_hal_stdin_uart_irq_init() and the UDC line by
        // dcd_baochip_irq_resume(), both on the next loop iteration.
        irq_disable(IRQ_UART);
        #if MICROPY_HW_ENABLE_USBDEV
        dcd_baochip_irq_pause();
        #endif
        mp_deinit();
    }
}

void gc_collect(void) {
    gc_collect_start();
    gc_helper_collect_regs_and_stack();
    gc_collect_end();
}

void nlr_jump_fail(void *val) {
    (void)val;
    for (;;) {
    }
}

mp_import_stat_t mp_import_stat(const char *path) {
    (void)path;
    return MP_IMPORT_STAT_NO_EXIST;
}

mp_lexer_t *mp_lexer_new_from_file(qstr filename) {
    (void)filename;
    mp_raise_OSError(MP_ENOENT);
}

mp_obj_t mp_builtin_open(size_t n_args, const mp_obj_t *args, mp_map_t *kwargs) {
    (void)n_args;
    (void)args;
    (void)kwargs;
    mp_raise_OSError(MP_ENOENT);
}
MP_DEFINE_CONST_FUN_OBJ_KW(mp_builtin_open_obj, 1, mp_builtin_open);
