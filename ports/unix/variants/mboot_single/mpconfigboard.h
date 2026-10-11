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

#ifndef MICROPY_INCLUDED_UNIX_VARIANTS_MBOOT_SINGLE_MPCONFIGBOARD_H
#define MICROPY_INCLUDED_UNIX_VARIANTS_MBOOT_SINGLE_MPCONFIGBOARD_H

// MCUboot configuration of the unix mboot_single variant, in the form of a board's
// mpconfigboard.h (see shared/mboot/include/mboot_layout.h): the single slot policy with the
// security counter in flash. The flash is the file backed device of mboot_dev.h.

#define MBOOT_PRIMARY_SIZE (0x40000)
#define MBOOT_ROLLBACK_COUNTER (1)
#define MBOOT_POLICY (MBOOT_POLICY_SEL_SINGLE)
#define MBOOT_DFU (0)
#define MBOOT_FSLOAD_LFS2 (1)

#endif // MICROPY_INCLUDED_UNIX_VARIANTS_MBOOT_SINGLE_MPCONFIGBOARD_H
