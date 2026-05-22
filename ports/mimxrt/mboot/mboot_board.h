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

#ifndef MICROPY_INCLUDED_PORTS_MIMXRT_MBOOT_MBOOT_BOARD_H
#define MICROPY_INCLUDED_PORTS_MIMXRT_MBOOT_MBOOT_BOARD_H

// mboot_board.h — port-level defaults for the mimxrt mboot bootloader.
//
// Board-specific values (VID/PID, flash base, product string) are provided in
// boards/<BOARD>/mboot_board.h, which the build system force-includes before
// each source file via -include $(BOARD_DIR)/mboot_board.h in the Makefile.
// This header sets defaults for anything not overridden by the board.

#include CPU_HEADER_H

// ---------------------------------------------------------------------------
// Magic-RAM entry key
// ---------------------------------------------------------------------------
#include "mboot_api.h"

// ---------------------------------------------------------------------------
// Double-tap-to-bootloader detection
// ---------------------------------------------------------------------------

// MBOOT_DBL_TAP_REG — persistent lvalue (uint32_t) surviving all reset types.
// Port default: SNVS->LPGPR[2] (battery-backed, survives power cycles and all
// software resets).  Board may override with a different register or RAM word.
// Note: SNVS->LPGPR[3] is reserved for the MBOOT_INITIAL_R0_KEY mechanism.
#ifndef MBOOT_DBL_TAP_REG
#define MBOOT_DBL_TAP_REG (SNVS->LPGPR[2])
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

// MBOOT_RAM_FUNC_ATTR is supplied via the mboot build's -D flags
// (see ports/mimxrt/mboot/Makefile); contract documented in
// shared/tinyusb/mboot/include/mboot_api.h.

#endif // MICROPY_INCLUDED_PORTS_MIMXRT_MBOOT_MBOOT_BOARD_H
