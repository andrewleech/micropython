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

#ifndef MICROPY_INCLUDED_UNIX_VARIANTS_MCUBOOT_MPCONFIGBOARD_H
#define MICROPY_INCLUDED_UNIX_VARIANTS_MCUBOOT_MPCONFIGBOARD_H

// MCUboot configuration of the unix mcuboot variant, in the form of a board's mpconfigboard.h
// (see shared/mcuboot/include/mcuboot_layout.h). The flash is the file backed device of
// mcuboot_dev.h.

#define MCUBOOT_PRIMARY_SIZE (0x40000)
#define MCUBOOT_SECONDARY_ADDR (0x90080000)
#define MCUBOOT_ROLLBACK_COUNTER (0)
#define MCUBOOT_DFU (0)
#define MCUBOOT_FSLOAD_LFS2 (1)

#endif // MICROPY_INCLUDED_UNIX_VARIANTS_MCUBOOT_MPCONFIGBOARD_H
