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

// mboot_boardctrl.h — app-side mboot entry hook for rp2 boards.
//
// Auto-included from ports/rp2/mpconfigport.h when USE_MBOOT is defined, so
// boards do not need to mention it.  Pulled in very early in compilation,
// before py/mpconfig.h has resolved qstrs, MP_INT_MAX,
// MICROPY_ROM_TEXT_COMPRESSION, and the rest of the core py/ configuration.
// It must NOT include py/obj.h or any other py/ header.
//
// All it does is:
//   1. Forward-declare rp2_enter_mboot_or_rom(), whose implementation lives in
//      ports/rp2/mboot_entry.c (compiled as a normal firmware source file,
//      where full py/ headers are available).
//   2. Define MICROPY_BOARD_ENTER_BOOTLOADER to call it.
//
// The macro is expanded inside mp_machine_bootloader() in ports/rp2/modmachine.c
// which already has full py/ context, so mp_obj_is_true() is safe there.

#ifndef MICROPY_INCLUDED_RP2_MBOOT_BOARDCTRL_H
#define MICROPY_INCLUDED_RP2_MBOOT_BOARDCTRL_H

#include <stdbool.h>
#include <stddef.h>

// rp2_enter_mboot_or_rom — write the mboot magic and reboot, or fall through
// to the ROM BOOTSEL path.
//
// to_rom == false (default): write 0x70AD0000 to watchdog_hw->scratch[0] and
//   call watchdog_reboot(0, 0, 0).  Does not return.
// to_rom == true: return immediately so that the existing reset_usb_boot()
//   BOOTSEL path in modmachine.c executes.
//
// Implemented in ports/rp2/mboot_entry.c.
extern void rp2_enter_mboot_or_rom(bool to_rom);

// MICROPY_BOARD_ENTER_BOOTLOADER — called from mp_machine_bootloader() in
// modmachine.c with full py/ context available.  mp_obj_is_true() is safe here.
#define MICROPY_BOARD_ENTER_BOOTLOADER(n, a) \
    rp2_enter_mboot_or_rom((n) > 0 && mp_obj_is_true((a)[0]))

#endif // MICROPY_INCLUDED_RP2_MBOOT_BOARDCTRL_H
