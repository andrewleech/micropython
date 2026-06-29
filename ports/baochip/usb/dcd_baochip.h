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

// Workaround for a Corigine UDC hardware bug: occasionally the IRQ
// chain stops delivering edges while the controller continues to
// write events to the event ring.  At wedge time we have:
//
//   USBSTS.EINT = 1   (controller asserting "event interrupt pending")
//   IMAN.IE     = 1   (interrupter enable still set)
//   IMAN.IP     = 0   (NOT asserting -- propagation broken)
//   EV_PENDING  = 0   (irqarray sees no pending event)
//
// Software-level re-arming of IMAN (W1C IP, re-write IE, blanket
// EV_PENDING clear, EV_ENABLE re-arm) all empirically make the wedge
// fire faster -- the race is in a layer below software.  See
// notes/dabao-port-investigation.md (2026-06-14 entries) and
// notes/captures/usbmon-wedge-2026-06-14.pcap for the wire-level
// evidence: EP0 control transfers (typically SET_CONTROL_LINE_STATE
// at CDC port open/close) take 5 s to complete and Linux unlinks them
// with -ENOENT.
//
// This function is the agreed workaround.  It reads USBSTS.EINT and,
// if set, runs dcd_int_handler() directly.  Wired into the standard
// MicroPython event pump via MICROPY_INTERNAL_EVENT_HOOK
// (mpconfigport.h), so it runs on every mp_event_handle_nowait() and
// mp_event_wait_ms() call -- the same sites where other ports pump
// port-specific event work.  Cost in the healthy path is one register
// read.
//
// This function must remain; without it the device wedges under sustained
// CDC churn (see notes/corigine-udc-irq-bug-report.md).
void dcd_baochip_poll_pending_events(void);

#endif // MICROPY_INCLUDED_BAOCHIP_USB_DCD_BAOCHIP_H
