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

// Static memory for idle task (required when configSUPPORT_STATIC_ALLOCATION=1)
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
// Static memory for timer task (required if software timers are enabled)
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

#if configCHECK_FOR_STACK_OVERFLOW
// Stack overflow hook - called when stack overflow is detected
void vApplicationStackOverflowHook(TaskHandle_t xTask, char *pcTaskName) {
    (void)xTask;
    (void)pcTaskName;
    // Halt on stack overflow
    __asm volatile ("cpsid i");
    for (;;) {
    }
}
#endif

#if configUSE_TICK_HOOK
// Tick hook - called from FreeRTOS xPortSysTickHandler (via xTaskIncrementTick)
// Note: uwTick is already updated in our SysTick_Handler before calling
// xPortSysTickHandler, so we don't need to update it here.
void vApplicationTickHook(void) {
    // Hook available for any additional per-tick processing if needed
}
#endif

#endif // MICROPY_PY_THREAD
