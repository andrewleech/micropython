/*
 * This file is part of the MicroPython project, http://micropython.org/
 *
 * The MIT License (MIT)
 *
 * Copyright (c) 2025 MicroPython Developers
 *
 * Zephyr Kernel Configuration for ARM Cortex-M (Bare-Metal)
 *
 * This file provides fixed CONFIG_ definitions for running Zephyr kernel
 * on bare-metal ARM Cortex-M targets (e.g., QEMU mps2-an385, STM32).
 *
 * Key differences from POSIX configuration:
 * - No CONFIG_ARCH_POSIX (bare-metal ARM architecture)
 * - No __thread TLS workarounds (Zephyr's native TLS works correctly)
 * - No MPU (many Cortex-M products lack MPU)
 * - Uses PendSV for context switching (not pthreads)
 * - Single-core assumptions hold true (no _current races)
 */

#ifndef MICROPY_INCLUDED_EXTMOD_ZEPHYR_KERNEL_CONFIG_CORTEX_M_H
#define MICROPY_INCLUDED_EXTMOD_ZEPHYR_KERNEL_CONFIG_CORTEX_M_H

// Include devicetree fixup BEFORE any CONFIG defines
// This must be first to stub out problematic DT macros
#include "zephyr/devicetree_fixup.h"

// Device-specific CMSIS definitions (required before including CMSIS headers)
// These would normally come from device-specific headers like stm32f4xx.h
#ifndef __NVIC_PRIO_BITS
#define __NVIC_PRIO_BITS 3U  // Cortex-M3 has 3 bits of priority (8 levels)
#endif

// IRQ number enum (minimal for QEMU mps2-an385)
// Guard C-only constructs from assembly preprocessing
#ifndef _ASMLANGUAGE
typedef enum {
    // Cortex-M3 system exceptions
    Reset_IRQn            = -15,
    NonMaskableInt_IRQn   = -14,
    HardFault_IRQn        = -13,
    MemoryManagement_IRQn = -12,
    BusFault_IRQn         = -11,
    UsageFault_IRQn       = -10,
    SVCall_IRQn           = -5,
    DebugMonitor_IRQn     = -4,
    PendSV_IRQn           = -2,
    SysTick_IRQn          = -1,
    // External interrupts (device-specific)
    UART0_IRQn            = 0,
    UART1_IRQn            = 1,
    UART2_IRQn            = 2,
    // Add more as needed for QEMU mps2-an385
} IRQn_Type;
#endif /* _ASMLANGUAGE */

// Core kernel features
#define CONFIG_MULTITHREADING 1
// Note: CONFIG_USE_SWITCH is not supported on ARM Cortex-M
// ARM Cortex-M uses custom main thread switching
#define CONFIG_ARCH_HAS_CUSTOM_SWAP_TO_MAIN 1
#define CONFIG_NUM_PREEMPT_PRIORITIES 15
#define CONFIG_NUM_COOP_PRIORITIES 16
#define CONFIG_MAIN_STACK_SIZE 10240  // 10KB for main thread (matches mp_stack_set_limit)
#define CONFIG_MAIN_THREAD_PRIORITY 1  // Slightly lower priority than user threads (0)
#define CONFIG_IDLE_STACK_SIZE 512
#define CONFIG_ISR_STACK_SIZE 2048
#define CONFIG_THREAD_STACK_INFO 1
#define CONFIG_KERNEL_LOG_LEVEL 0

// Thread configuration
#define CONFIG_THREAD_CUSTOM_DATA 1
#define CONFIG_THREAD_NAME 1
#define CONFIG_THREAD_MAX_NAME_LEN 32
#define CONFIG_THREAD_MONITOR 0
#define CONFIG_DYNAMIC_THREAD 1

// Scheduler configuration
#define CONFIG_SCHED_DUMB 0
#define CONFIG_SCHED_SCALABLE 1
#define CONFIG_SCHED_MULTIQ 0
#define CONFIG_WAITQ_SCALABLE 1
#define CONFIG_WAITQ_DUMB 0
#undef CONFIG_SCHED_CPU_MASK

// SMP configuration (disabled - single-core only)
// Note: #ifndef checks in kernel_structs.h require CONFIG_SMP to be undefined, not 0
#undef CONFIG_SMP
#define CONFIG_MP_NUM_CPUS 1
#define CONFIG_MP_MAX_NUM_CPUS CONFIG_MP_NUM_CPUS

// Timing and clock
#define CONFIG_SYS_CLOCK_EXISTS 1
#define CONFIG_SYS_CLOCK_TICKS_PER_SEC 1000
#define CONFIG_SYS_CLOCK_HW_CYCLES_PER_SEC 1000000  // 1MHz (microseconds)
#define CONFIG_SYS_CLOCK_MAX_TIMEOUT_DAYS 365
#define CONFIG_TIMER_READS_ITS_FREQUENCY_AT_RUNTIME 0
#define CONFIG_SYSTEM_CLOCK_SLOPPY_IDLE 0
#define CONFIG_SYSTEM_CLOCK_INIT_PRIORITY 0
#define CONFIG_TICKLESS_KERNEL 0
#define CONFIG_TIMEOUT_64BIT 1

// Timeslicing
#define CONFIG_TIMESLICING 1
#define CONFIG_TIMESLICE_SIZE 0  // Disabled - using priority-based preemption instead
#define CONFIG_TIMESLICE_PRIORITY 0

// Memory and heap
#define CONFIG_KERNEL_MEM_POOL 0
#define CONFIG_HEAP_MEM_POOL_SIZE 0
#define CONFIG_HEAP_MEM_POOL_IGNORE_MIN 1

// CRITICAL DISABLES - These eliminate generated header dependencies
// CONFIG_USERSPACE must NOT be defined (not even as 0) to disable syscall marshalling
#undef CONFIG_USERSPACE                     // Ensure it's not defined - disables syscall system
#define CONFIG_MMU 0
#define CONFIG_DEMAND_PAGING 0
#define CONFIG_DEMAND_PAGING_STATS 0
#define CONFIG_MEM_ATTR 0
#define CONFIG_TIMER_READS_ITS_FREQUENCY_AT_RUNTIME 0  // Disables time_units syscalls

// Userspace-related configs (not used but headers reference them)
#define CONFIG_MAX_DOMAIN_PARTITIONS 0
#define CONFIG_MAX_THREAD_BYTES 0

// Device and device tree - NOT NEEDED for pure threading
#define CONFIG_DEVICE_DT_METADATA 0
#define CONFIG_DEVICE_DEPS 0

// Object core (for introspection) - disabled for minimal footprint
#define CONFIG_OBJ_CORE 0
#define CONFIG_OBJ_CORE_THREAD 0
#define CONFIG_OBJ_CORE_STATS 0
#define CONFIG_OBJ_CORE_STATS_THREAD 0

// Synchronization primitives
#undef CONFIG_POLL
#undef CONFIG_EVENTS

// IRQ and interrupt configuration
#define CONFIG_IRQ_OFFLOAD 0
#define CONFIG_ATOMIC_OPERATIONS_C 0
#define CONFIG_ATOMIC_OPERATIONS_BUILTIN 1

// Architecture-specific (will be overridden by arch layer if needed)
#define CONFIG_ISR_STACK_SIZE 2048
#define CONFIG_ISR_SUBSTACK_SIZE 2048
#define CONFIG_ISR_DEPTH 1
// Stack safety features - must be undefined (not 0) for #ifdef checks
#undef CONFIG_REQUIRES_STACK_CANARIES
#undef CONFIG_STACK_CANARIES
#undef CONFIG_STACK_SENTINEL
#undef CONFIG_THREAD_STACK_MEM_MAPPED

// Logging and debugging - disabled for production
#define CONFIG_LOG 0
#define CONFIG_LOG_MODE_MINIMAL 1
#define CONFIG_ASSERT 1
#define CONFIG_SPIN_VALIDATE 0
#undef CONFIG_ARCH_HAS_THREAD_NAME_HOOK

// Boot arguments
#define CONFIG_BOOTARGS 0

// Thread usage monitoring
#define CONFIG_SCHED_THREAD_USAGE 1
#define CONFIG_SCHED_THREAD_USAGE_ALL 0

// FPU support (must use #undef for #if defined() checks)
#undef CONFIG_FPU
#undef CONFIG_FPU_SHARING

// Errno configuration
#define CONFIG_ERRNO 1
#define CONFIG_ERRNO_IN_TLS 0
#define CONFIG_LIBC_ERRNO 1

// Zephyr version: Now generated from VERSION file via gen_zephyr_version.py
// See extmod/zephyr_kernel/generated/zephyr/version.h

// Priority queue configuration
#define CONFIG_NUM_METAIRQ_PRIORITIES 0
#define CONFIG_PRIORITY_CEILING -127  // No priority ceiling

// Logging configuration (disabled)
#define CONFIG_LOG_MAX_LEVEL 0

// Thread usage tracking
#define CONFIG_SCHED_THREAD_USAGE_ALL 0
#define CONFIG_SCHED_THREAD_USAGE_AUTO_ENABLE 0

// Work queue (not used yet, but kernel may reference)
#define CONFIG_SYSTEM_WORKQUEUE_PRIORITY -1
#define CONFIG_SYSTEM_WORKQUEUE_STACK_SIZE 1024

// Device configuration (disabled - not needed for threading-only)
#undef CONFIG_DEVICE  // Must not be defined to disable device support
#define CONFIG_DEVICE_DT_METADATA 0
#define CONFIG_DEVICE_DEPS 0

// Initialize priority
#define CONFIG_KERNEL_INIT_PRIORITY_OBJECTS 30
#define CONFIG_KERNEL_INIT_PRIORITY_DEFAULT 40
#define CONFIG_KERNEL_INIT_PRIORITY_DEVICE 50

// ===========================================================================
// ARM Cortex-M Architecture Configuration
// ===========================================================================

// ARM architecture
#define CONFIG_ARM 1
#define CONFIG_CPU_CORTEX_M 1
#define CONFIG_CPU_CORTEX_M3 1  // Specify exact Cortex-M variant
#define CONFIG_ARMV7_M_ARMV8_M_MAINLINE 1  // Cortex-M3/M4/M7/M33
#define CONFIG_ARCH "arm"
#define CONFIG_ASSEMBLER_ISA_THUMB2 1  // Cortex-M uses Thumb-2 instruction set

// ARM-specific options
#define CONFIG_ARCH_HAS_CUSTOM_BUSY_WAIT 1
#define CONFIG_ARCH_HAS_THREAD_ABORT 1
#define CONFIG_ARCH_HAS_SUSPEND_TO_RAM 0

// MPU configuration - EXPLICITLY DISABLED
// Many Cortex-M products don't have MPU, and we don't need it for threading
#undef CONFIG_ARM_MPU
#undef CONFIG_MPU
#undef CONFIG_MPU_REQUIRES_POWER_OF_TWO_ALIGNMENT
#undef CONFIG_MPU_GAP_FILLING

// ARM interrupt configuration
#define CONFIG_NUM_IRQS 48  // Number of external IRQs for mps2-an385
#define CONFIG_ZERO_LATENCY_IRQS 0
#define CONFIG_SW_ISR_TABLE 1
#define CONFIG_SW_ISR_TABLE_DYNAMIC 1
#define CONFIG_GEN_ISR_TABLES 1
#define CONFIG_GEN_IRQ_VECTOR_TABLE 1

// ARM exception configuration
// Must use #undef (not #define 0) because Zephyr uses #if defined() checks
#undef CONFIG_ARM_SECURE_FIRMWARE
#undef CONFIG_ARM_NONSECURE_FIRMWARE
#define CONFIG_GEN_SW_ISR_TABLE 1

// ARM FP configuration
// Cortex-M3 has no FPU - must use #undef (not #define 0) for #if defined() checks
#undef CONFIG_CPU_HAS_FPU  // Enable for Cortex-M4F/M7 if needed
#define CONFIG_FP_HARDABI 0
#define CONFIG_FP_SOFTABI 1

// Memory addresses for Cortex-M
#define CONFIG_PRIVILEGED_STACK_SIZE 1024
#define CONFIG_KERNEL_VM_BASE 0
#define CONFIG_KERNEL_VM_OFFSET 0
#define CONFIG_SRAM_BASE_ADDRESS 0
#define CONFIG_SRAM_OFFSET 0

// Ensure we're not in unit test mode (so __syscall becomes "static inline")
#ifdef ZTEST_UNITTEST
#undef ZTEST_UNITTEST
#endif

// Z_SYSCALL macros - stub out userspace validation
#define Z_SYSCALL_DECLARE(...)
#define Z_SYSCALL_HANDLER(...)

// Architecture detection for other platforms
#if !defined(CONFIG_ARM) && !defined(CONFIG_CPU_CORTEX_M)
#error "This configuration file is for ARM Cortex-M only. Use zephyr_config.h for POSIX."
#endif

#endif // MICROPY_INCLUDED_EXTMOD_ZEPHYR_KERNEL_CONFIG_CORTEX_M_H
