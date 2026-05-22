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

// mboot_entry.c — implementation of rp2_enter_mboot_or_rom().
//
// This file is compiled as a normal rp2 firmware source file and therefore has
// full access to the Pico SDK hardware headers.  The function is called from
// mp_machine_bootloader() in modmachine.c via the MICROPY_BOARD_ENTER_BOOTLOADER
// macro defined in mboot_boardctrl.h.

#include "py/mpconfig.h"

#if defined(USE_MBOOT)

#include <stdbool.h>

#include "hardware/structs/watchdog.h"
#include "hardware/watchdog.h"
// mboot_api.h defines MBOOT_INITIAL_R0_KEY.  Use a path relative to the
// MicroPython root (which is on the include path as ${MICROPY_DIR} / $(TOP)).
#include "shared/tinyusb/mboot/include/mboot_api.h"

void rp2_enter_mboot_or_rom(bool to_rom) {
    if (!to_rom) {
        // Write the mboot-entry magic to watchdog scratch[0].  mboot reads
        // this register in mboot_port_early_init and clears it before proceeding.
        watchdog_hw->scratch[0] = MBOOT_INITIAL_R0_KEY;
        watchdog_reboot(0, 0, 0);
        for (;;) {
            ;
        }
    }
    // to_rom == true: return so that the ROM reset_usb_boot() BOOTSEL path
    // in modmachine.c executes.
}

#endif // defined(USE_MBOOT)
