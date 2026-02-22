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

#if MICROPY_BLUETOOTH_ZEPHYR_CONTROLLER

#include "zephyr_ble_irq.h"
#include "nrf.h"  // nrfx MDK — provides NVIC functions and IRQn_Type

static zephyr_ble_irq_entry_t irq_table[ZEPHYR_BLE_IRQ_TABLE_SIZE];

// Debug: count ISR dispatches
static volatile uint32_t isr_dispatch_count;

void irq_enable(unsigned int irq) {
    NVIC_EnableIRQ((IRQn_Type)irq);
}

void irq_disable(unsigned int irq) {
    NVIC_DisableIRQ((IRQn_Type)irq);
}

int irq_is_enabled(unsigned int irq) {
    return NVIC_GetEnableIRQ((IRQn_Type)irq);
}

int irq_connect_dynamic(unsigned int irq, unsigned int priority,
    zephyr_ble_isr_t isr, const void *param,
    uint32_t flags) {
    if (irq >= ZEPHYR_BLE_IRQ_TABLE_SIZE) {
        return -1;
    }
    irq_table[irq].isr = isr;
    irq_table[irq].param = param;
    irq_table[irq].priority = priority;
    irq_table[irq].direct = (flags & 1);  // ISR_FLAG_DIRECT
    NVIC_SetPriority((IRQn_Type)irq, priority);
    return 0;
}

void zephyr_ble_irq_dispatch(unsigned int irq) {
    isr_dispatch_count++;
    if (irq < ZEPHYR_BLE_IRQ_TABLE_SIZE && irq_table[irq].isr) {
        irq_table[irq].isr(irq_table[irq].param);
    } else {
        // Unhandled IRQ — print once
        static uint32_t unhandled_mask;
        if (irq < 32 && !(unhandled_mask & (1u << irq))) {
            unhandled_mask |= (1u << irq);
            // Can't safely print from ISR, but for debug:
            // Toggle a GPIO or use RTT instead
        }
    }
}

uint32_t zephyr_ble_irq_dispatch_count(void) {
    return isr_dispatch_count;
}

#endif // MICROPY_BLUETOOTH_ZEPHYR_CONTROLLER
