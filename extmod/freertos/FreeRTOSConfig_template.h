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
 * FreeRTOS Configuration Template for MicroPython Threading Backend
 *
 * Copy this file to your port directory as FreeRTOSConfig.h and customize
 * the values marked with [PORT] for your specific hardware.
 *
 * See FREERTOS_THREADING_REQUIREMENTS.md Section 5 for detailed explanation.
 */

#ifndef FREERTOS_CONFIG_H
#define FREERTOS_CONFIG_H

// ============================================================================
// [PORT] Hardware-specific settings - MUST be customized for your port
// ============================================================================

// CPU clock frequency in Hz - get from your HAL or define per board
// Example: #define configCPU_CLOCK_HZ (SystemCoreClock)
// [PORT] MUST define configCPU_CLOCK_HZ before including this template
#ifndef configCPU_CLOCK_HZ
#error "configCPU_CLOCK_HZ must be defined by the port before including FreeRTOSConfig.h"
#endif

// Cortex-M interrupt priorities (lower number = higher priority)
// These values are hardware-specific and MUST match your port's interrupt setup
// For Cortex-M3/M4/M7 with 4 priority bits: priorities 0-15, use 5 for kernel
#define configLIBRARY_LOWEST_INTERRUPT_PRIORITY         (15)
#define configLIBRARY_MAX_SYSCALL_INTERRUPT_PRIORITY    (5)

// Derived priority values for Cortex-M (shift for actual register values)
// [PORT] Adjust __NVIC_PRIO_BITS for your MCU (typically 3-4)
#define configKERNEL_INTERRUPT_PRIORITY         (configLIBRARY_LOWEST_INTERRUPT_PRIORITY << (8 - /* __NVIC_PRIO_BITS */ 4))
#define configMAX_SYSCALL_INTERRUPT_PRIORITY    (configLIBRARY_MAX_SYSCALL_INTERRUPT_PRIORITY << (8 - /* __NVIC_PRIO_BITS */ 4))

// ============================================================================
// MANDATORY settings for MicroPython threading backend
// DO NOT change these values
// ============================================================================

// Required for GC-first memory strategy - allocate TCB/stack via m_new()
#define configSUPPORT_STATIC_ALLOCATION (1)

// Thread-local storage for mp_state_thread_t pointer
#define configNUM_THREAD_LOCAL_STORAGE_POINTERS (1)

// Required for mp_thread_mutex_* functions
#define configUSE_MUTEXES (1)

// Required for recursive mutex support (GIL)
#define configUSE_RECURSIVE_MUTEXES (1)

// Required for thread cleanup on exit
#define INCLUDE_vTaskDelete (1)

// Required for mp_thread_get_id() and main thread adoption
#define INCLUDE_xTaskGetCurrentTaskHandle (1)

// ============================================================================
// RECOMMENDED settings for MicroPython
// Adjust for your port's requirements
// ============================================================================

// 1ms tick for time.sleep_ms() precision
#define configTICK_RATE_HZ (1000)

// Preemptive scheduling required for threading
#define configUSE_PREEMPTION (1)

// Priority levels: Idle(0), Python threads(1-2), USB/Network(3+)
#define configMAX_PRIORITIES (8)

// Minimum stack size for idle task and system tasks (in words, not bytes)
#define configMINIMAL_STACK_SIZE (128)

// Maximum task name length (for debugging)
#define configMAX_TASK_NAME_LEN (16)

// Enable tick hook for potential event handling
#define configUSE_TICK_HOOK (0)

// Enable idle hook for low-power modes
#define configUSE_IDLE_HOOK (0)

// Time slicing for equal-priority threads
#define configUSE_TIME_SLICING (1)

// ============================================================================
// OPTIONAL features - enable as needed
// ============================================================================

// Stack overflow detection (useful for debugging, disable in release)
#define configCHECK_FOR_STACK_OVERFLOW (2)

// Stack high water mark for debugging stack usage
#define INCLUDE_uxTaskGetStackHighWaterMark (1)

// Task notifications (efficient signaling)
#define configUSE_TASK_NOTIFICATIONS (1)

// Timer service task (enable if using software timers)
#define configUSE_TIMERS (0)
#if configUSE_TIMERS
#define configTIMER_TASK_PRIORITY (configMAX_PRIORITIES - 1)
#define configTIMER_QUEUE_LENGTH (10)
#define configTIMER_TASK_STACK_DEPTH (configMINIMAL_STACK_SIZE * 2)
#endif

// Counting semaphores (if needed)
#define configUSE_COUNTING_SEMAPHORES (1)

// Queue sets (if needed for multi-queue waiting)
#define configUSE_QUEUE_SETS (0)

// ============================================================================
// Memory allocation configuration
// Note: Actual thread memory uses MicroPython GC heap, not FreeRTOS heap
// ============================================================================

// Dynamic allocation enabled only for idle/timer tasks (via vApplicationGetIdleTaskMemory)
// NOTE: Python threads MUST use static allocation (xTaskCreateStatic) with GC memory.
// The FreeRTOS heap is NOT used for Python thread stacks.
#define configSUPPORT_DYNAMIC_ALLOCATION (1)

// Small heap for idle task only (if dynamic allocation enabled)
#define configTOTAL_HEAP_SIZE (4096)

// ============================================================================
// SMP configuration (for multi-core MCUs like RP2040)
// ============================================================================

// Number of cores - set to 1 for single-core, 2 for dual-core
#define configNUMBER_OF_CORES (1)

#if configNUMBER_OF_CORES > 1
// SMP-specific settings
#define configUSE_CORE_AFFINITY (1)
#define configRUN_MULTIPLE_PRIORITIES (1)
#endif

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
// Assert configuration for debugging
// ============================================================================

// [PORT] Replace with your platform's assert mechanism
// This default halts on assertion failure - useful for debugging
#ifndef configASSERT
#define configASSERT(x) do { if (!(x)) { __asm volatile ("cpsid i"); for (;;) {} } } while (0)
#endif

// ============================================================================
// Cortex-M specific definitions
// ============================================================================

// Use optimized task selection for Cortex-M (CLZ instruction)
#define configUSE_PORT_OPTIMISED_TASK_SELECTION (1)

// PendSV and SysTick handlers - [PORT] ensure these match your startup code
#define xPortPendSVHandler PendSV_Handler
#define xPortSysTickHandler SysTick_Handler
#define vPortSVCHandler SVC_Handler

#endif // FREERTOS_CONFIG_H
