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
#include "py/mpprint.h"

// ============================================================================
// FreeRTOS interrupt handlers
// RP2040 FreeRTOS port uses configUSE_DYNAMIC_EXCEPTION_HANDLERS to install
// its own SVC, PendSV, and SysTick handlers. We do not need to provide them.
// ============================================================================

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

// For SMP (configNUMBER_OF_CORES > 1), provide passive idle task memory for core 1
#if configNUMBER_OF_CORES > 1
static StaticTask_t xPassiveIdleTaskTCB;
static StackType_t xPassiveIdleTaskStack[configMINIMAL_STACK_SIZE];

void vApplicationGetPassiveIdleTaskMemory(StaticTask_t **ppxIdleTaskTCBBuffer,
    StackType_t **ppxIdleTaskStackBuffer,
    configSTACK_DEPTH_TYPE *puxIdleTaskStackSize,
    BaseType_t xPassiveIdleTaskIndex) {
    (void)xPassiveIdleTaskIndex;
    *ppxIdleTaskTCBBuffer = &xPassiveIdleTaskTCB;
    *ppxIdleTaskStackBuffer = xPassiveIdleTaskStack;
    *puxIdleTaskStackSize = configMINIMAL_STACK_SIZE;
}
#endif

// ============================================================================
// Stack overflow hook
// ============================================================================

#if configCHECK_FOR_STACK_OVERFLOW
void vApplicationStackOverflowHook(TaskHandle_t xTask, char *pcTaskName) {
    // Print diagnostic information before halting
    mp_printf(&mp_plat_print, "\n\n!!! STACK OVERFLOW DETECTED !!!\n");
    mp_printf(&mp_plat_print, "Task: %s (handle=%p)\n", pcTaskName, xTask);

    #if INCLUDE_uxTaskGetStackHighWaterMark
    UBaseType_t highWaterMark = uxTaskGetStackHighWaterMark(xTask);
    mp_printf(&mp_plat_print, "Stack high water mark: %u words remaining\n", (unsigned int)highWaterMark);
    #endif

    // Read current stack pointer (ARM Cortex-M)
    register uint32_t sp_val __asm("sp");
    mp_printf(&mp_plat_print, "Current SP: %p\n", (void *)sp_val);

    mp_printf(&mp_plat_print, "System halted.\n\n");

    // Halt CPU with interrupts disabled
    __asm volatile ("cpsid i");
    for (;;) {
    }
}
#endif

#endif // MICROPY_PY_THREAD
