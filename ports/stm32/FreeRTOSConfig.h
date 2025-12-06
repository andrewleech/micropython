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

/*
 * FreeRTOS Configuration for STM32 MicroPython port
 */

#ifndef FREERTOS_CONFIG_H
#define FREERTOS_CONFIG_H

#include <stdint.h>

// Most STM32 have 4 NVIC priority bits
// This will be overridden by CMSIS if included later
#ifndef __NVIC_PRIO_BITS
#define __NVIC_PRIO_BITS 4
#endif

// Get clock frequency from HAL
extern uint32_t SystemCoreClock;
#define configCPU_CLOCK_HZ (SystemCoreClock)

// Cortex-M interrupt priorities
// STM32F4/F7 have 4 priority bits (0-15), lower number = higher priority
// SysTick and PendSV should be at lowest priority for FreeRTOS
#define configLIBRARY_LOWEST_INTERRUPT_PRIORITY         (15)
#define configLIBRARY_MAX_SYSCALL_INTERRUPT_PRIORITY    (5)

// Derived values (shifted for NVIC registers)
#define configKERNEL_INTERRUPT_PRIORITY         (configLIBRARY_LOWEST_INTERRUPT_PRIORITY << (8 - __NVIC_PRIO_BITS))
#define configMAX_SYSCALL_INTERRUPT_PRIORITY    (configLIBRARY_MAX_SYSCALL_INTERRUPT_PRIORITY << (8 - __NVIC_PRIO_BITS))

// ============================================================================
// MANDATORY for MicroPython threading backend
// ============================================================================

#define configSUPPORT_STATIC_ALLOCATION (1)
#define configNUM_THREAD_LOCAL_STORAGE_POINTERS (1)
#define configUSE_MUTEXES (1)
#define configUSE_RECURSIVE_MUTEXES (1)
#define INCLUDE_vTaskDelete (1)
#define INCLUDE_xTaskGetCurrentTaskHandle (1)

// ============================================================================
// Scheduler configuration
// ============================================================================

#define configTICK_RATE_HZ (1000)
#define configUSE_PREEMPTION (1)
#define configUSE_16_BIT_TICKS (0)
#define configMAX_PRIORITIES (8)
#define configMINIMAL_STACK_SIZE (128)
#define configMAX_TASK_NAME_LEN (16)
#define configUSE_TICK_HOOK (1)
#define configUSE_IDLE_HOOK (0)
#define configUSE_TIME_SLICING (1)

// ============================================================================
// Memory configuration
// ============================================================================

// Dynamic allocation enabled for idle/timer tasks
// Python threads use static allocation with GC memory
#define configSUPPORT_DYNAMIC_ALLOCATION (1)
#define configTOTAL_HEAP_SIZE (4096)

// ============================================================================
// Optional features
// ============================================================================

#define configCHECK_FOR_STACK_OVERFLOW (2)
#define INCLUDE_uxTaskGetStackHighWaterMark (1)
#define configUSE_TASK_NOTIFICATIONS (1)
#define configUSE_COUNTING_SEMAPHORES (1)
#define configUSE_QUEUE_SETS (0)

// Timers disabled to save space (enable if needed)
#define configUSE_TIMERS (0)

// ============================================================================
// Include optional function APIs
// ============================================================================

#define INCLUDE_vTaskPrioritySet (1)
#define INCLUDE_uxTaskPriorityGet (1)
#define INCLUDE_vTaskDelay (1)
#define INCLUDE_vTaskDelayUntil (1)
#define INCLUDE_vTaskSuspend (1)
#define INCLUDE_xTaskGetSchedulerState (1)
#define INCLUDE_xTaskResumeFromISR (1)
#define INCLUDE_eTaskGetState (1)

// ============================================================================
// Assert configuration
// ============================================================================

#define configASSERT(x) do { if (!(x)) { __asm volatile ("cpsid i"); for (;;) {} } } while (0)

// ============================================================================
// Cortex-M specific
// ============================================================================

#define configUSE_PORT_OPTIMISED_TASK_SELECTION (1)

// Disable handler installation check - our SVC/PendSV handlers wrap the
// FreeRTOS handlers, so the vector table addresses won't match exactly.
// The wrappers correctly forward to vPortSVCHandler/xPortPendSVHandler.
#define configCHECK_HANDLER_INSTALLATION (0)

// Handler integration note:
// DO NOT define xPortPendSVHandler, xPortSysTickHandler, vPortSVCHandler here.
// The STM32 port keeps its own handlers and calls FreeRTOS functions as needed.
// This preserves existing port functionality (pendsv_dispatch, systick_dispatch, etc).

#endif // FREERTOS_CONFIG_H
