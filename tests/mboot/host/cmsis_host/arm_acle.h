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

#ifndef MICROPY_INCLUDED_TESTS_MBOOT_HOST_CMSIS_HOST_ARM_ACLE_H
#define MICROPY_INCLUDED_TESTS_MBOOT_HOST_CMSIS_HOST_ARM_ACLE_H

// Lets the host compiler read the CMSIS headers of an STM32 device. The host builds of
// ports/stm32/mboot/mcuboot/mboot_dev.h take the flash and SRAM geometry from them. CMSIS includes
// <arm_acle.h> first and then needs the architecture macros of an Arm compiler. This directory is
// added with -idirafter, so an Arm compiler finds its own <arm_acle.h> first and this file is
// only used on the host. Only the inline functions of the CMSIS core headers use the intrinsics,
// and the host builds never call them.

#define __ARM_ARCH_PROFILE 'M'
#define __ARM_ARCH 8

#endif // MICROPY_INCLUDED_TESTS_MBOOT_HOST_CMSIS_HOST_ARM_ACLE_H
