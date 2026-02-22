/*
 * This file is part of the MicroPython project, http://micropython.org/
 *
 * The MIT License (MIT)
 *
 * Copyright (c) 2025 MicroPython Contributors
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

#ifndef MICROPY_INCLUDED_ZEPHYR_BLE_IRQ_H
#define MICROPY_INCLUDED_ZEPHYR_BLE_IRQ_H

#include <stdint.h>
#include <stdbool.h>

// nRF52840 has IRQ numbers up to ~47 (SPIM3_IRQn = 47)
#define ZEPHYR_BLE_IRQ_TABLE_SIZE 48

// IRQ handler function type (matches Zephyr's isr_t)
typedef void (*zephyr_ble_isr_t)(const void *param);

// Dynamic IRQ connection table entry
typedef struct {
    zephyr_ble_isr_t isr;
    const void *param;
    uint8_t priority;
    bool direct;  // Direct ISR (no parameter passing)
} zephyr_ble_irq_entry_t;

// IRQ management functions (Zephyr API compatibility)
void irq_enable(unsigned int irq);
void irq_disable(unsigned int irq);
int irq_is_enabled(unsigned int irq);
int irq_connect_dynamic(unsigned int irq, unsigned int priority,
    zephyr_ble_isr_t isr, const void *param,
    uint32_t flags);

// Dispatch function called from ISR stubs
void zephyr_ble_irq_dispatch(unsigned int irq);

// Zephyr utility functions (find_lsb_set, find_msb_set)
// In real Zephyr these come via the arch-specific include chain.
// We include directly since our stub chain doesn't pull them in.
#include <zephyr/arch/common/ffs.h>

// Zephyr IRQ_CONNECT macro — connects ISR at compile time
// In our cooperative implementation, this uses irq_connect_dynamic at runtime.
#ifndef IRQ_CONNECT
#define IRQ_CONNECT(irq_p, priority_p, isr_p, isr_param_p, flags_p) \
    irq_connect_dynamic(irq_p, priority_p, (zephyr_ble_isr_t)(isr_p), \
                        (const void *)(isr_param_p), flags_p)
#endif

// IRQ_DIRECT_CONNECT — same as IRQ_CONNECT but marks ISR as direct (no param)
#ifndef IRQ_DIRECT_CONNECT
#define IRQ_DIRECT_CONNECT(irq_p, priority_p, isr_p, flags_p) \
    irq_connect_dynamic(irq_p, priority_p, (zephyr_ble_isr_t)(isr_p), NULL, flags_p)
#endif

// ISR_DIRECT_HEADER / ISR_DIRECT_FOOTER — no-ops in our implementation
#ifndef ISR_DIRECT_HEADER
#define ISR_DIRECT_HEADER()
#endif
#ifndef ISR_DIRECT_FOOTER
#define ISR_DIRECT_FOOTER(x) (void)(x)
#endif

// ISR_DIRECT_PM — power management return flag (no-op)
#ifndef ISR_DIRECT_PM
#define ISR_DIRECT_PM() (1)
#endif

// ISR_DIRECT_DECLARE — declares a direct ISR function.
// In real Zephyr ARM, this creates a wrapper with __attribute__((interrupt))
// and an inline body function. Our version simplifies to a regular void function
// since our dispatch table handles ISR entry/exit.
#ifndef ISR_DIRECT_DECLARE
#define ISR_DIRECT_DECLARE(name) \
    static inline int name##_body(void); \
    void name(void) \
    { \
        int check_reschedule; \
        ISR_DIRECT_HEADER(); \
        check_reschedule = name##_body(); \
        ISR_DIRECT_FOOTER(check_reschedule); \
    } \
    static inline int name##_body(void)
#endif

#endif // MICROPY_INCLUDED_ZEPHYR_BLE_IRQ_H
