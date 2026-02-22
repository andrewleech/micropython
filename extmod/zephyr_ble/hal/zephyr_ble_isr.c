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

// ISR stubs for Zephyr BLE controller on nRF52840.
// These override the weak aliases in device/startup_nrf52840.c.
// The controller registers its ISR functions via irq_connect_dynamic()
// which stores them in the IRQ dispatch table.
//
// IMPORTANT: Dispatch indices must use the actual IRQn values from the nRF MDK,
// not assumed sequential numbers. On nRF52840 the SWI/EGU IRQs start at 20
// (SWI0_EGU0) but SWI4_EGU4=24 and SWI5_EGU5=25.

#if MICROPY_BLUETOOTH_ZEPHYR_CONTROLLER && defined(NRF52840_XXAA)

#include "zephyr_ble_irq.h"
#include "nrf.h"

// RADIO — radio timing, highest priority
volatile uint32_t radio_isr_count;
void RADIO_IRQHandler(void) {
    radio_isr_count++;
    zephyr_ble_irq_dispatch(RADIO_IRQn);
}

// TIMER0 — radio timing support
void TIMER0_IRQHandler(void) {
    zephyr_ble_irq_dispatch(TIMER0_IRQn);
}

// RTC0 — controller ticker
volatile uint32_t rtc0_isr_count;
void RTC0_IRQHandler(void) {
    rtc0_isr_count++;
    zephyr_ble_irq_dispatch(RTC0_IRQn);
}

// RNG — hardware random number generator
void RNG_IRQHandler(void) {
    zephyr_ble_irq_dispatch(RNG_IRQn);
}

// ECB — AES encryption
void ECB_IRQHandler(void) {
    zephyr_ble_irq_dispatch(ECB_IRQn);
}

// CCM_AAR — crypto / address resolution
void CCM_AAR_IRQHandler(void) {
    zephyr_ble_irq_dispatch(CCM_AAR_IRQn);
}

// SWI4/EGU4 — LLL mayfly
volatile uint32_t swi4_isr_count;
void SWI4_EGU4_IRQHandler(void) {
    swi4_isr_count++;
    zephyr_ble_irq_dispatch(SWI4_EGU4_IRQn);
}

// SWI5/EGU5 — ULL low mayfly
void SWI5_EGU5_IRQHandler(void) {
    zephyr_ble_irq_dispatch(SWI5_EGU5_IRQn);
}

#endif // MICROPY_BLUETOOTH_ZEPHYR_CONTROLLER && NRF52840_XXAA
