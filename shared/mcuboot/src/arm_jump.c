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

#include "mcuboot_port.h"

// Only for Cortex-M targets; other targets provide their own mcuboot_port_jump().
#if defined(__arm__)

#define SCB_VTOR (*(volatile uint32_t *)0xE000ED08u)

__attribute__((weak, noreturn)) void mcuboot_port_jump(uint32_t vt_addr) {
    const uint32_t *vt = (const uint32_t *)vt_addr;
    uint32_t msp = vt[0];
    uint32_t entry = vt[1];

    __asm volatile ("cpsid i" ::: "memory");
    SCB_VTOR = vt_addr;
    // Interrupts are enabled again just before the branch: the application starts with
    // PRIMASK = 0 as after a reset (the port's deinit leaves every IRQ disabled and cleared).
    __asm volatile (
        "dsb\n"
        "isb\n"
        "msr msp, %0\n"
        "cpsie i\n"
        "bx %1\n"
        :
        : "r" (msp), "r" (entry)
        : "memory");
    for (;;) {
    }
}

#endif // __arm__
