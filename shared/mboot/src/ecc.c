/*
 * This file is part of the MicroPython project, http://micropython.org/
 *
 * The MIT License (MIT)
 *
 * Copyright (c) 2026 Andrew Leech
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

// Event counters for the ECC of an internal flash (a device with MICROPY_HW_MBOOT_DEV0_ECC), on
// top of the two flags the controller of the port reports (mboot_port_ecc_detect_take() and
// mboot_port_ecc_corrected_take()). Both roles.
//
// A read of a flash word with an invalid ECC returns the data and raises the NMI with the
// detection flag set. The NMI handler of the bootloader (fault.c) and of the application call
// mboot_ecc_nmi() and return. A word left partly programmed by a power cut can instead be
// corrected on read: no NMI, and the data is the erased value or a partial program. That flag
// is sticky, so each look at the counter that finds it set counts one correction and clears it.

#include "mboot_layout.h"

#if MICROPY_HW_MBOOT_DEV0_ECC

#include "mboot_port.h"

static volatile uint32_t s_nmi_count;
static volatile uint32_t s_nmi_info;
static volatile uint32_t s_corrected_count;

bool mboot_ecc_nmi(void) {
    uint32_t info;
    if (!mboot_port_ecc_detect_take(&info)) {
        return false;
    }
    s_nmi_info = info;
    s_nmi_count++;
    return true;
}

uint32_t mboot_port_ecc_corrected(void) {
    if (mboot_port_ecc_corrected_take()) {
        s_corrected_count++;
    }
    return s_corrected_count;
}

// A flag the NMI handler has not consumed yet counts as an event. That happens when the NMI is
// still pending or the caller runs in an NMI or fault handler.
uint32_t mboot_port_ecc_events(void) {
    mboot_ecc_nmi();
    return s_nmi_count;
}

void mboot_port_ecc_clear(void) {
    mboot_ecc_nmi();
}

uint32_t mboot_ecc_last_info(void) {
    return s_nmi_info;
}

#endif
