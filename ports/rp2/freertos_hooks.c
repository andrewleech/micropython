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

#include "py/mpconfig.h"

#if MICROPY_PY_THREAD

#include "FreeRTOS.h"
#include "task.h"

// ============================================================================
// FreeRTOS interrupt handlers
// These wrap FreeRTOS port.c functions for integration with the vector table
// IMPORTANT: SVC and PendSV must be naked and branch directly - wrapping
// in a C function call corrupts the exception stack frame.
// ============================================================================

// FreeRTOS handler declarations from port.c
extern void vPortSVCHandler(void);
extern void xPortPendSVHandler(void);
extern void xPortSysTickHandler(void);

// SVC handler - naked branch to FreeRTOS handler
__attribute__((naked)) void SVC_Handler(void) {
    __asm volatile (
        "b vPortSVCHandler\n"
        );
}

// PendSV handler - naked branch to FreeRTOS handler
__attribute__((naked)) void PendSV_Handler(void) {
    __asm volatile (
        "b xPortPendSVHandler\n"
        );
}

// Millisecond tick counter (accessed by ticks_ms())
extern volatile uint32_t _ticks_ms;

// SysTick handler - updates tick counter and calls FreeRTOS
void SysTick_Handler(void) {
    // Update millisecond counter
    _ticks_ms++;

    // Call FreeRTOS tick handler if scheduler is running
    if (xTaskGetSchedulerState() != taskSCHEDULER_NOT_STARTED) {
        xPortSysTickHandler();
    }
}

// ============================================================================
// Static memory allocation callbacks (required when configSUPPORT_STATIC_ALLOCATION=1)
// ============================================================================

static StaticTask_t xIdleTaskTCB;
static StackType_t xIdleTaskStack[configMINIMAL_STACK_SIZE];

void vApplicationGetIdleTaskMemory(StaticTask_t **ppxIdleTaskTCBBuffer,
    StackType_t **ppxIdleTaskStackBuffer,
    configSTACK_DEPTH_TYPE *puxIdleTaskStackSize) {
    *ppxIdleTaskTCBBuffer = &xIdleTaskTCB;
    *ppxIdleTaskStackBuffer = xIdleTaskStack;
    *puxIdleTaskStackSize = configMINIMAL_STACK_SIZE;
}

#if configUSE_TIMERS
static StaticTask_t xTimerTaskTCB;
static StackType_t xTimerTaskStack[configTIMER_TASK_STACK_DEPTH];

void vApplicationGetTimerTaskMemory(StaticTask_t **ppxTimerTaskTCBBuffer,
    StackType_t **ppxTimerTaskStackBuffer,
    configSTACK_DEPTH_TYPE *puxTimerTaskStackSize) {
    *ppxTimerTaskTCBBuffer = &xTimerTaskTCB;
    *ppxTimerTaskStackBuffer = xTimerTaskStack;
    *puxTimerTaskStackSize = configTIMER_TASK_STACK_DEPTH;
}
#endif

// ============================================================================
// Stack overflow hook
// ============================================================================

#if configCHECK_FOR_STACK_OVERFLOW
void vApplicationStackOverflowHook(TaskHandle_t xTask, char *pcTaskName) {
    (void)xTask;
    (void)pcTaskName;
    // Halt on stack overflow
    __asm volatile ("cpsid i");
    for (;;) {
    }
}
#endif

#endif // MICROPY_PY_THREAD
