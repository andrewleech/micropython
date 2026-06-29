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

// This file is never compiled standalone, it's included directly from
// extmod/modmachine.c via MICROPY_PY_MACHINE_INCLUDEFILE.

#include "py/mphal.h"

#include "bao/platform.h"
#include "bao_reset.h"
#include "hardware/gpio.h"
#include "machine_pin.h"

#if MICROPY_HW_ENABLE_USBDEV
#include "usb/dcd_baochip.h"

// Manually trigger the deferred USB controller re-bring-up.  Escape hatch
// for the stochastic native-CDC wedge that leaves no firmware-visible USB
// signal: the re-init runs on the next scheduler tick and briefly (~0.5 s)
// stalls the scheduler while the controller re-brings-up and the host
// re-enumerates.
static mp_obj_t machine_usb_reinit(void) {
    dcd_baochip_request_reinit();
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_0(machine_usb_reinit_obj, machine_usb_reinit);

#define MICROPY_PY_MACHINE_EXTRA_GLOBALS \
    { MP_ROM_QSTR(MP_QSTR_Pin), MP_ROM_PTR(&machine_pin_type) }, \
    { MP_ROM_QSTR(MP_QSTR_usb_reinit), MP_ROM_PTR(&machine_usb_reinit_obj) }, \

#else
#define MICROPY_PY_MACHINE_EXTRA_GLOBALS \
    { MP_ROM_QSTR(MP_QSTR_Pin), MP_ROM_PTR(&machine_pin_type) }, \

#endif

static void mp_machine_idle(void) {
    __asm__ volatile ("wfi");
}

MP_NORETURN static void mp_machine_reset(void) {
    bao_software_reset();
    for (;;) {
    }
}

static mp_int_t mp_machine_reset_cause(void) {
    // TODO: read the SCG (SoC Control / reset cause) register and map
    // to the standard mp_machine_reset_cause values (PWRON / HARD /
    // SOFT / WDT / DEEPSLEEP).
    return 0;
}

// Drive PC13 (PROG strap) LOW then reset so boot1 enters its CDC REPL.
// PC13 is dual-use on Dabao: it is BOTH the boot strap AND the
// board-level USB SE0 shunt (driving it low forces D+/D- to ground,
// signalling a real wire-level disconnect to the host).  The DCD
// teardown below uses that to give the host time to see disconnect
// before the chip reset, and leaves PC13 driven low so PROG is
// asserted at reset.  Without the teardown, boot1 inherits a live USB
// pull-up from our DCD and the host never registers a clean
// disconnect-then-reconnect transition, which on the tentacle's
// TinyUSB host manifests as stuck CDC bulk endpoints.
MP_NORETURN void mp_machine_bootloader(size_t n_args, const mp_obj_t *args) {
    (void)n_args;
    (void)args;
    #if MICROPY_HW_ENABLE_USBDEV
    // Drives PC13 low, holds 100 ms for SE0 to register, soft-resets the
    // UDC, zeroes IFRAM.
    dcd_baochip_teardown();
    #else
    // No USB DCD running -- just assert PROG and reset.
    gpio_init(pin_PC13->port, pin_PC13->pin);
    gpio_put(pin_PC13->port, pin_PC13->pin, false);
    gpio_set_dir(pin_PC13->port, pin_PC13->pin, true);
    mp_hal_delay_us(1000);
    #endif
    bao_software_reset();
    for (;;) {
    }
}

static mp_obj_t mp_machine_unique_id(void) {
    // TODO: expose the per-chip serial via IFR or boot1 metadata.
    mp_raise_NotImplementedError(NULL);
}

static mp_obj_t mp_machine_get_freq(void) {
    return mp_obj_new_int_from_uint(ACLK_HZ);
}

static void mp_machine_set_freq(size_t n_args, const mp_obj_t *args) {
    (void)n_args;
    (void)args;
    // TODO: implement clock tree reconfiguration via the SDK clk helpers.
    mp_raise_NotImplementedError(NULL);
}

static void mp_machine_lightsleep(size_t n_args, const mp_obj_t *args) {
    if (n_args > 0) {
        // TODO: implement timeout via TickTimer wakeup.
        (void)args;
        mp_raise_NotImplementedError(MP_ERROR_TEXT("timeout"));
    }
    __asm__ volatile ("wfi");
}

MP_NORETURN static void mp_machine_deepsleep(size_t n_args, const mp_obj_t *args) {
    (void)n_args;
    (void)args;
    // TODO: enter the SoC's deep-sleep state via the SDK pmu helpers.
    for (;;) {
        __asm__ volatile ("wfi");
    }
}
