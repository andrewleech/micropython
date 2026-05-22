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

// mboot_dbl_tap.h — shared double-tap-to-bootloader detection.
//
// Ports opt in by defining MBOOT_DBL_TAP_REG in mboot_board.h (port level)
// or boards/<BOARD>/mboot_board.h (board level).  When the board header is
// force-included at compile time (via -include), the board definition wins
// over the port default.
//
// Required port/board macros (define in mboot_board.h):
//
//   MBOOT_DBL_TAP_REG
//     A persistent lvalue (uint32_t) that survives a soft/watchdog reset.
//     Typically a word in a .noinit / .uninitialized_data RAM section, or a
//     hardware scratchpad register (e.g. an RTC backup register).
//     On RP2350 the RUN-pin reset clears SRAM; boards needing RUN-pin
//     double-tap should map this to POWMAN->chip_reset via a suitable
//     accessor macro.
//
// Optional overrides (provide in mboot_board.h to change defaults):
//
//   MBOOT_DBL_TAP_MAGIC      — magic sentinel (default 0xf01669ef, TinyUF2-compatible)
//   MBOOT_DBL_TAP_DELAY_MS   — detection window in ms (default 500)

#pragma once

#include <stdbool.h>

// mboot_dbl_tap_check — detect a double-tap reset and signal DFU entry.
//
// On first call (single tap): arms MBOOT_DBL_TAP_REG with MBOOT_DBL_TAP_MAGIC,
// busy-waits MBOOT_DBL_TAP_DELAY_MS for a second reset, then disarms and
// returns false.
//
// On second call within the window (double tap): MBOOT_DBL_TAP_REG already
// holds the magic; clears it and returns true.
//
// Should only be called when a valid application is present; skip for empty
// flash so the device enters DFU immediately without the delay.
//
// Requires: MBOOT_DBL_TAP_REG defined, mboot_port_delay_ms() available.
bool mboot_dbl_tap_check(void);
