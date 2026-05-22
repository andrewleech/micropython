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

#ifndef MICROPY_INCLUDED_PORTS_RP2_MBOOT_MBOOT_BOARD_H
#define MICROPY_INCLUDED_PORTS_RP2_MBOOT_MBOOT_BOARD_H

// XIP_BASE is used in APPLICATION_ADDR below; pull it in unconditionally so
// that any includer that doesn't already have hardware/regs/addressmap.h gets
// a proper definition rather than a confusing "XIP_BASE undeclared" error.
#include "hardware/regs/addressmap.h"

// mboot_board.h — port-level defaults for the rp2 mboot bootloader.
//
// Per-board overrides come from the board's mpconfigboard.h (the same file
// used by the main rp2 firmware build) inside a `#if defined(USE_MBOOT)`
// block.  The mboot CMakeLists.txt force-includes that header before any
// source file is compiled, so any board-defined MBOOT_* macro is visible by
// the time this header is processed.  This file then supplies #ifndef'd
// defaults for everything the board does not override.

// ---------------------------------------------------------------------------
// Flash layout
// ---------------------------------------------------------------------------

// MBOOT_RESERVED — number of bytes at the start of flash reserved for mboot.
// The application must be linked to start at XIP_BASE + MBOOT_RESERVED.
// Default: 64 KiB (16 sectors of 4 KiB).
#ifndef MBOOT_RESERVED
#define MBOOT_RESERVED (64 * 1024)
#endif

// APPLICATION_ADDR — start address of the application image in XIP flash.
#ifndef APPLICATION_ADDR
#define APPLICATION_ADDR (XIP_BASE + MBOOT_RESERVED)
#endif

// ---------------------------------------------------------------------------
// USB identity defaults (overridden per-board)
// ---------------------------------------------------------------------------

#ifndef MBOOT_USB_VID
#define MBOOT_USB_VID (0x2E8A)
#endif

#ifndef MBOOT_USB_PID
#define MBOOT_USB_PID (0xDF00)
#endif

#ifndef MBOOT_PRODUCT_STRING
#define MBOOT_PRODUCT_STRING "RP2 Bootloader"
#endif

// ---------------------------------------------------------------------------
// LED
// ---------------------------------------------------------------------------

// MBOOT_LED_PIN — GPIO number of the status LED (active-high).
// Set to -1 to disable LED support.
#ifndef MBOOT_LED_PIN
#define MBOOT_LED_PIN (-1)
#endif

// ---------------------------------------------------------------------------
// Boot button
// ---------------------------------------------------------------------------

// MBOOT_BOOTPIN — GPIO number of the boot/user button (active-low with pull-up).
// Set to -1 if no boot pin is wired.
#ifndef MBOOT_BOOTPIN
#define MBOOT_BOOTPIN (-1)
#endif

// ---------------------------------------------------------------------------
// Magic-RAM entry key
// ---------------------------------------------------------------------------

// MBOOT_INITIAL_R0_KEY — imported from the shared mboot API header.
// The canonical definition lives in shared/tinyusb/mboot/include/mboot_api.h.
// shared/tinyusb/mboot/include/ is on the include path in the mboot build.
#include "mboot_api.h"

// MBOOT_RAM_FUNC_ATTR is supplied via the mboot build's -D flags
// (see ports/rp2/mboot/CMakeLists.txt); contract documented in
// shared/tinyusb/mboot/include/mboot_api.h.

// ---------------------------------------------------------------------------
// Double-tap-to-bootloader detection
// ---------------------------------------------------------------------------

// MBOOT_DBL_TAP_REG — persistent lvalue (uint32_t) surviving watchdog/soft resets.
// Port default: word in .uninitialized_data RAM (not zeroed by C runtime).
// Board may override with a hardware scratchpad register.
// Note: on RP2350, a RUN-pin reset clears all SRAM, so boards requiring
// double-tap over a RUN-pin reset should override with POWMAN->chip_reset.
#ifndef MBOOT_DBL_TAP_REG
extern uint32_t mboot_dbl_tap_reg;
#define MBOOT_DBL_TAP_REG mboot_dbl_tap_reg
#endif

// MBOOT_DBL_TAP_MAGIC — sentinel value written to MBOOT_DBL_TAP_REG on first tap.
// Matches TinyUF2's UF2_DBL_TAP_MAGIC for cross-bootloader compatibility.
#ifndef MBOOT_DBL_TAP_MAGIC
#define MBOOT_DBL_TAP_MAGIC (0xf01669efu)
#endif

// MBOOT_DBL_TAP_DELAY_MS — detection window between first and second tap.
#ifndef MBOOT_DBL_TAP_DELAY_MS
#define MBOOT_DBL_TAP_DELAY_MS (500u)
#endif

#endif // MICROPY_INCLUDED_PORTS_RP2_MBOOT_MBOOT_BOARD_H
