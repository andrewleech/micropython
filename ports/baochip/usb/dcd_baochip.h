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

#ifndef MICROPY_INCLUDED_BAOCHIP_USB_DCD_BAOCHIP_H
#define MICROPY_INCLUDED_BAOCHIP_USB_DCD_BAOCHIP_H

#include <stdint.h>
#include "dcd_corigine_udc.h"

// Register the UDC IRQ vector and enable its event bits.  Called once
// after irq_init(), before the TinyUSB stack starts.
void dcd_baochip_irq_register(void);

// Tear down the Corigine UDC ahead of a chip-level soft reset.
// Drives the board-level SE0 pin (PC13) low to signal disconnect, masks
// the UDC IRQ, halts and soft-resets the controller, and zeroes the
// IFRAM working set so boot1 inherits clean memory.  Leaves PC13
// driven low; the caller is expected to immediately call
// bao_software_reset() so the boot strap is asserted at reset.
//
// Safe to call when dcd_init has not run -- becomes a no-op aside from
// driving PC13 low.
void dcd_baochip_teardown(void);

// Mask / unmask the UDC interrupt at the CPU level across a MicroPython
// soft reset.  The DCD interrupt persists across soft reset (so the CDC
// stays enumerated) but its handler touches MicroPython scheduler state,
// so main() pauses it before mp_deinit() and resumes it after the next
// mp_usbd_init().  Both are no-ops until dcd_init has run.
void dcd_baochip_irq_pause(void);
void dcd_baochip_irq_resume(void);

// Request a deferred full USB controller re-bring-up, run from task
// context on the next scheduler tick.  Escape hatch for the stochastic
// native-CDC wedge that leaves no firmware-visible USB signal (see
// udc_perform_reinit); exposed as machine.usb_reinit().  No-op until
// dcd_init has run.
void dcd_baochip_request_reinit(void);

#endif // MICROPY_INCLUDED_BAOCHIP_USB_DCD_BAOCHIP_H
