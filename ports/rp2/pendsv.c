/*
 * This file is part of the MicroPython project, http://micropython.org/
 *
 * The MIT License (MIT)
 *
 * Copyright (c) 2022 Damien P. George
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

#include <assert.h>
#include "py/mpconfig.h"
#include "pendsv.h"

#if PICO_RP2040
#include "RP2040.h"
#elif PICO_RP2350 && PICO_ARM
#include "RP2350.h"
#elif PICO_RISCV
#include "pico/aon_timer.h"
#endif

#if MICROPY_PY_NETWORK_CYW43
#include "lib/cyw43-driver/src/cyw43_stats.h"
#endif

#if MICROPY_PY_THREAD && MICROPY_FREERTOS_SERVICE_TASKS

// ============================================================================
// FreeRTOS Service Task Implementation (using shared framework)
//
// Uses the shared mp_freertos_service framework from extmod/freertos/.
// This port provides wrappers that maintain the pendsv_* API and the
// required mp_freertos_service_in_isr() function.
// ============================================================================

#include "extmod/freertos/mp_freertos_service.h"

// Port-provided ISR context detection for Cortex-M (IPSR != 0 means exception)
bool mp_freertos_service_in_isr(void) {
    #if PICO_ARM
    uint32_t ipsr;
    __asm volatile ("mrs %0, ipsr" : "=r" (ipsr));
    return ipsr != 0;
    #elif PICO_RISCV
    // RISC-V: check machine cause register or similar
    // For now, assume we're not in ISR context for RISC-V
    // TODO: Implement proper RISC-V ISR detection
    return false;
    #else
    return false;
    #endif
}

void pendsv_init(void) {
    mp_freertos_service_init();
}

void pendsv_suspend(void) {
    mp_freertos_service_suspend();
}

void pendsv_resume(void) {
    mp_freertos_service_resume();
}

void pendsv_schedule_dispatch(size_t slot, pendsv_dispatch_t f) {
    #if MICROPY_PY_NETWORK_CYW43
    // Track CYW43 dispatch scheduling
    if (slot == PENDSV_DISPATCH_CYW43) {
        // Stats tracking handled in gpio_irq_handler
    }
    #endif
    mp_freertos_service_schedule(slot, f);
}

bool pendsv_is_pending(size_t slot) {
    return mp_freertos_service_is_pending(slot);
}

#else // !MICROPY_PY_THREAD || !MICROPY_FREERTOS_SERVICE_TASKS

// ============================================================================
// Non-threaded Implementation (original PendSV-based approach)
//
// Without FreeRTOS, we use the traditional PendSV interrupt mechanism.
// ============================================================================

#include "hardware/irq.h"

// PendSV IRQ priority, to run system-level tasks that preempt the main thread.
#define IRQ_PRI_PENDSV PICO_LOWEST_IRQ_PRIORITY

static volatile pendsv_dispatch_t pendsv_dispatch_table[PENDSV_DISPATCH_NUM_SLOTS];
static int pendsv_lock;

void pendsv_init(void) {
    #if !defined(__riscv)
    NVIC_SetPriority(PendSV_IRQn, IRQ_PRI_PENDSV);
    #endif
}

void pendsv_suspend(void) {
    pendsv_lock++;
}

void pendsv_resume(void) {
    assert(pendsv_lock > 0);
    pendsv_lock--;

    // Re-trigger if work is pending
    if (pendsv_lock == 0) {
        for (size_t i = 0; i < PENDSV_DISPATCH_NUM_SLOTS; ++i) {
            if (pendsv_dispatch_table[i] != NULL) {
                #if PICO_ARM
                SCB->ICSR = SCB_ICSR_PENDSVSET_Msk;
                #elif PICO_RISCV
                struct timespec ts;
                aon_timer_get_time(&ts);
                aon_timer_enable_alarm(&ts, PendSV_Handler, false);
                #endif
                break;
            }
        }
    }
}

void pendsv_schedule_dispatch(size_t slot, pendsv_dispatch_t f) {
    pendsv_dispatch_table[slot] = f;

    if (pendsv_lock == 0) {
        #if PICO_ARM
        SCB->ICSR = SCB_ICSR_PENDSVSET_Msk;
        #elif PICO_RISCV
        struct timespec ts;
        aon_timer_get_time(&ts);
        aon_timer_enable_alarm(&ts, PendSV_Handler, false);
        #endif
    } else {
        #if MICROPY_PY_NETWORK_CYW43
        CYW43_STAT_INC(PENDSV_DISABLED_COUNT);
        #endif
    }
}

// PendSV interrupt handler for non-threaded builds
void PendSV_Handler(void) {
    assert(pendsv_lock == 0);

    #if MICROPY_PY_NETWORK_CYW43
    CYW43_STAT_INC(PENDSV_RUN_COUNT);
    #endif

    for (size_t i = 0; i < PENDSV_DISPATCH_NUM_SLOTS; ++i) {
        if (pendsv_dispatch_table[i] != NULL) {
            pendsv_dispatch_t f = pendsv_dispatch_table[i];
            pendsv_dispatch_table[i] = NULL;
            f();
        }
    }
}

bool pendsv_is_pending(size_t slot) {
    return pendsv_dispatch_table[slot] != NULL;
}

#endif // MICROPY_PY_THREAD && MICROPY_FREERTOS_SERVICE_TASKS
