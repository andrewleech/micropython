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

// MicroPython port glue for the Corigine UDC TinyUSB DCD.
// Handles IRQ registration, MicroPython scheduler integration,
// and port-supplied callbacks (time source, serial number).

#include "py/mpconfig.h"

#if MICROPY_HW_ENABLE_USBDEV

#include <string.h>

#include "py/mphal.h"
#include "py/runtime.h"

#include "hardware/irq.h"
#include "tusb_config.h"
#include "device/dcd.h"

#include "dcd_baochip.h"
#include "udc_regs.h"

// IRQ vector: trampoline into the TinyUSB DCD handler.
static void udc_irq_handler(uint32_t pending) {
    (void)pending;
    dcd_int_handler(0);
}

// Register the UDC IRQ vector and enable its event bits.  Called once
// after irq_init(), before the TinyUSB stack is started.
void dcd_baochip_irq_register(void) {
    irq_set_handler(IRQ_ARRAY1, udc_irq_handler);
    irq_enable_events(IRQ_ARRAY1, UDC_IRQARRAY_USBC_BIT | UDC_IRQARRAY_SW_BIT);
}

// Mask / unmask the UDC interrupt across a MicroPython soft reset so
// the DCD ISR cannot fire against a half-rebuilt runtime.
void dcd_baochip_irq_pause(void) {
    dcd_int_disable(0);
}

void dcd_baochip_irq_resume(void) {
    dcd_int_enable(0);
}

// Poll the UDC for pending events that the IRQ chain may have missed.
// Wired into MICROPY_INTERNAL_EVENT_HOOK (see mpconfigport.h).
void dcd_baochip_poll_pending_events(void) {
    dcd_corigine_poll_pending_events(0);
}

// Deferred full controller re-bring-up, scheduled via the MicroPython
// scheduler so it runs in task context, not inside an ISR.
static mp_sched_node_t udc_reinit_node;
static void udc_reinit_sched_cb(mp_sched_node_t *node) {
    (void)node;
    dcd_corigine_reinit(0);
}

void dcd_baochip_request_reinit(void) {
    mp_sched_schedule_node(&udc_reinit_node, udc_reinit_sched_cb);
}

// Tear down the UDC ahead of a chip-level soft reset.  Drives SE0,
// halts and soft-resets the controller, zeroes IFRAM.  SE0 (PC13) is
// left asserted on return so PROG is held during the chip reset.
void dcd_baochip_teardown(void) {
    dcd_corigine_teardown(0);
}

// TinyUSB time source.
uint32_t tusb_time_millis_api(void) {
    return (uint32_t)mp_hal_ticks_ms();
}

// USB serial number descriptor.  Placeholder until the chip UID
// register is surfaced.
void mp_usbd_port_get_serial_number(char *buf) {
    static const char placeholder[] = "BAOCHIP0000000001";
    memcpy(buf, placeholder, sizeof(placeholder));
}

#endif // MICROPY_HW_ENABLE_USBDEV
