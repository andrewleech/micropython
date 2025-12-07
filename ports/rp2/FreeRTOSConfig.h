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
 * FreeRTOS Configuration for RP2040 MicroPython port (Cortex-M0+, dual-core)
 *
 * NOTE: The RP2040 FreeRTOS SMP port uses SysTick for tick generation, which
 * is clocked from clk_sys. This means if machine.freq() changes the system clock,
 * the FreeRTOS tick rate will be affected. Consider this when changing frequency.
 */

#ifndef FREERTOS_CONFIG_H
#define FREERTOS_CONFIG_H

#include <stdint.h>

// RP2040 default frequency is 125MHz
#define configCPU_CLOCK_HZ (125000000u)

// Cortex-M0+ has 2 priority bits (4 levels, 0-3)
#define __NVIC_PRIO_BITS 2

// Cortex-M interrupt priorities
// SysTick and PendSV should be at lowest priority for FreeRTOS
#define configLIBRARY_LOWEST_INTERRUPT_PRIORITY         (3)
#define configLIBRARY_MAX_SYSCALL_INTERRUPT_PRIORITY    (2)

// Derived values (shifted for NVIC registers)
#define configKERNEL_INTERRUPT_PRIORITY         (configLIBRARY_LOWEST_INTERRUPT_PRIORITY << (8 - __NVIC_PRIO_BITS))
#define configMAX_SYSCALL_INTERRUPT_PRIORITY    (configLIBRARY_MAX_SYSCALL_INTERRUPT_PRIORITY << (8 - __NVIC_PRIO_BITS))

// ============================================================================
// SMP (Symmetric Multiprocessing) configuration
// ============================================================================

#define configNUMBER_OF_CORES (2)
#define configUSE_CORE_AFFINITY (1)
#define configRUN_MULTIPLE_PRIORITIES (1)
#define configUSE_PASSIVE_IDLE_HOOK (0)

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
#define configUSE_TICK_HOOK (0)
#define configUSE_IDLE_HOOK (0)
#define configUSE_TIME_SLICING (1)

// ============================================================================
// Memory configuration
// ============================================================================

// RP2040 has 264KB RAM total - use conservative heap size
// Most memory for Python threads comes from MicroPython GC heap
#define configSUPPORT_DYNAMIC_ALLOCATION (1)
#define configTOTAL_HEAP_SIZE (8192)

// ============================================================================
// Optional features
// ============================================================================

#define configCHECK_FOR_STACK_OVERFLOW (2)
#define INCLUDE_uxTaskGetStackHighWaterMark (1)
#define configUSE_TASK_NOTIFICATIONS (1)
#define configUSE_COUNTING_SEMAPHORES (1)
#define configUSE_QUEUE_SETS (0)

// Timers disabled to save RAM
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

// M0+ doesn't have CLZ instruction, so use generic task selection
#define configUSE_PORT_OPTIMISED_TASK_SELECTION (0)

// Disable handler installation check - our handlers wrap the FreeRTOS handlers
#define configCHECK_HANDLER_INSTALLATION (0)

#endif // FREERTOS_CONFIG_H
