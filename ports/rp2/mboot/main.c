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

// main.c — bootloader entry point for the rp2 mboot image.

#include <stdint.h>

#include "tusb.h"

#include "mboot_api.h"
#include "mboot_board.h"
#include "mboot_dbl_tap.h"
#include "mboot_dfu.h"
#include "mboot_region.h"
#include "mboot_usbd.h"

int main(void) {
    uint32_t initial_r0 = 0;

    // 1. Early hardware init: clocks, watchdog scratch decode, LED init.
    mboot_port_early_init(&initial_r0);

    // 2. Check for forced entry (hardware button or magic-RAM key).
    bool forced = mboot_port_entry_forced();

    // 3. Also consider programmatic entry via magic-RAM scratch register.
    if (!forced && initial_r0 == MBOOT_INITIAL_R0_KEY) {
        forced = true;
    }

    // 4. Double-tap detection: only when a valid application is present.
    //    Skip when already forced so we don't add a 500 ms delay on button boot.
    if (!forced && mboot_port_app_valid()) {
        forced = mboot_dbl_tap_check();
    }

    // 5. Check the UI-selected reset mode.
    int reset_mode = mboot_port_get_reset_mode();

    if (!forced && reset_mode == MBOOT_RESET_MODE_NORMAL && mboot_port_app_valid()) {
        // Fast path: no DFU activity requested; jump to the application.
        mboot_port_cleanup(MBOOT_RESET_MODE_NORMAL);
        mboot_port_leave(MBOOT_RESET_MODE_NORMAL);
        // mboot_port_leave is NORETURN; unreachable.
    }

    // 6. Initialise LED, DFU state machine, region table, USB descriptors.
    mboot_port_led_init();
    mboot_dfu_init();
    mboot_region_init();
    mboot_usbd_init();

    // 7. Start TinyUSB device stack.
    tusb_init();

    // 8. Main DFU service loop.
    for (;;) {
        tud_task();

        if (mboot_usbd_leave_requested()) {
            // DFU manifest complete: boot the freshly-flashed application.
            mboot_port_cleanup(MBOOT_RESET_MODE_NORMAL);
            mboot_port_leave(MBOOT_RESET_MODE_NORMAL);
            // mboot_port_leave is NORETURN; unreachable.
        }
    }
}
