/*
 * This file is part of the MicroPython project, http://micropython.org/
 *
 * The MIT License (MIT)
 *
 * Copyright (c) 2025 Damien P. George
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
#ifndef MICROPY_INCLUDED_EXTMOD_ZEPHYR_KERNEL_ARCH_IRQ_PRIMASK_H
#define MICROPY_INCLUDED_EXTMOD_ZEPHYR_KERNEL_ARCH_IRQ_PRIMASK_H

// Override Zephyr's BASEPRI-based arch_irq_lock() with PRIMASK-based version
// for complete interrupt masking in critical sections.
//
// ISSUE: Zephyr's default arch_irq_lock() uses BASEPRI register on Cortex-M4/M7,
// which masks only interrupts with priority >= threshold (0x10). This allows
// priority 0 interrupts to fire during critical sections, potentially corrupting
// thread state during mutex operations, context switches, and scheduler operations.
//
// FIX: Use PRIMASK register which masks ALL configurable interrupts (except NMI
// and HardFault), providing complete interrupt masking in critical sections.
//
// TRADE-OFF: Slightly longer interrupt latency for priority 0 interrupts during
// critical sections, but eliminates race conditions and memory corruption.

#undef arch_irq_lock
#undef arch_irq_unlock

static ALWAYS_INLINE unsigned int arch_irq_lock(void) {
    unsigned int key = __get_PRIMASK();
    __disable_irq();  // Sets PRIMASK=1, disables ALL configurable interrupts
    return key;
}

static ALWAYS_INLINE void arch_irq_unlock(unsigned int key) {
    if (key != 0U) {
        return;  // Interrupts were already disabled, don't re-enable
    }
    __enable_irq();  // Sets PRIMASK=0, enables interrupts
    __ISB();
}

#endif // MICROPY_INCLUDED_EXTMOD_ZEPHYR_KERNEL_ARCH_IRQ_PRIMASK_H
