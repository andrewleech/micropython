/*
 * This file is part of the MicroPython project, http://micropython.org/
 *
 * The MIT License (MIT)
 *
 * Copyright (c) 2025 MicroPython Developers
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
#ifndef MICROPY_ZEPHYR_IRQ_HANDLERS_H
#define MICROPY_ZEPHYR_IRQ_HANDLERS_H

/*
 * Zephyr Threading IRQ Handler Functions
 *
 * These functions provide the Zephyr-specific tick and context switch handling.
 * Ports should call these from their existing IRQ handlers when
 * MICROPY_ZEPHYR_THREADING is enabled.
 *
 * PORT INTEGRATION PRINCIPLE:
 *   Ports retain ownership of their IRQ handlers. When integrating Zephyr
 *   threading, ports should NOT replace their existing SysTick_Handler or
 *   PendSV_Handler. Instead, they should call these handler functions from
 *   within their existing handlers.
 *
 * USAGE PATTERN:
 *
 *   // In port's SysTick_Handler
 *   void SysTick_Handler(void) {
 *       // Existing port tick processing first
 *       port_systick_process();
 *
 *       #if MICROPY_ZEPHYR_THREADING
 *       // Then call Zephyr thread handler
 *       mp_zephyr_systick_thread_handler();
 *       #endif
 *   }
 *
 *   // In port's PendSV_Handler (must be naked attribute)
 *   __attribute__((naked)) void PendSV_Handler(void) {
 *       #if MICROPY_ZEPHYR_THREADING
 *       // Zephyr context switch - does not return
 *       mp_zephyr_pendsv_thread_handler();
 *       #else
 *       // Existing port handler or error handler
 *       existing_pendsv_code();
 *       #endif
 *   }
 */

#if MICROPY_ZEPHYR_THREADING

/*
 * mp_zephyr_systick_thread_handler()
 *
 * Call from port's SysTick_Handler when MICROPY_ZEPHYR_THREADING is enabled.
 *
 * This function:
 * - Increments the Zephyr tick counter
 * - Calls sys_clock_announce() to process timeouts and wake sleeping threads
 * - Checks if a higher-priority thread is ready and triggers PendSV if needed
 *
 * The port's SysTick_Handler should call any port-specific tick processing
 * (e.g., uwTick increment, soft timer handling) BEFORE calling this function.
 */
void mp_zephyr_systick_thread_handler(void);

/*
 * mp_zephyr_pendsv_thread_handler()
 *
 * Call from port's PendSV_Handler when MICROPY_ZEPHYR_THREADING is enabled.
 *
 * IMPORTANT: This function does NOT return. It directly branches to Zephyr's
 * z_arm_pendsv assembly routine which performs the context switch and returns
 * to the switched-to thread.
 *
 * The calling PendSV_Handler must have the 'naked' attribute to ensure the
 * correct stack frame for Zephyr's context switch code.
 *
 * For inline asm branch:
 *   __asm volatile ("b z_arm_pendsv");
 */
void mp_zephyr_pendsv_thread_handler(void);

#endif // MICROPY_ZEPHYR_THREADING

#endif // MICROPY_ZEPHYR_IRQ_HANDLERS_H
