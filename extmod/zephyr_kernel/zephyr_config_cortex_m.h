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

// Include devicetree stubs FIRST to prevent devicetree macro expansion errors
#include <zephyr/devicetree_fixup.h>

// Include STM32 CMSIS device headers for IRQn_Type, __NVIC_PRIO_BITS, etc.
// CMSIS_MCU is defined via -D flag from Makefile (e.g., -DSTM32F429xx)
// This makes the config header self-contained and ensures offsets.c can compile
// Skip C headers when compiling assembly files
#if !defined(__ASSEMBLER__) && !defined(_ASMLANGUAGE)
#if defined(STM32F429xx)
#include "stm32f429xx.h"
#elif defined(STM32F407xx)
#include "stm32f407xx.h"
#elif defined(STM32F405xx)
#include "stm32f405xx.h"
#elif defined(STM32F411xE)
#include "stm32f411xe.h"
#elif defined(STM32F413xx)
#include "stm32f413xx.h"
#elif defined(STM32F446xx)
#include "stm32f446xx.h"
#elif defined(STM32F722xx)
#include "stm32f722xx.h"
#elif defined(STM32F733xx)
#include "stm32f733xx.h"
#elif defined(STM32F746xx)
#include "stm32f746xx.h"
#elif defined(STM32F756xx)
#include "stm32f756xx.h"
#elif defined(STM32F767xx)
#include "stm32f767xx.h"
#elif defined(STM32F769xx)
#include "stm32f769xx.h"
#elif defined(STM32H743xx)
#include "stm32h743xx.h"
#elif defined(STM32H747xx)
#include "stm32h747xx.h"
#elif defined(STM32H750xx)
#include "stm32h750xx.h"
#elif defined(STM32H7A3xx)
#include "stm32h7a3xx.h"
#elif defined(STM32H7A3xxQ)
#include "stm32h7a3xxq.h"
#elif defined(STM32H7B3xx)
#include "stm32h7b3xx.h"
#elif defined(STM32H7B3xxQ)
#include "stm32h7b3xxq.h"
#elif defined(STM32H723xx)
#include "stm32h723xx.h"
#elif defined(STM32H573xx)
#include "stm32h573xx.h"
#elif defined(STM32L432xx)
#include "stm32l432xx.h"
#elif defined(STM32L452xx)
#include "stm32l452xx.h"
#elif defined(STM32L476xx)
#include "stm32l476xx.h"
#elif defined(STM32WB55xx)
#include "stm32wb55xx.h"
#elif defined(STM32WL55xx)
#include "stm32wl55xx.h"
#else
// For non-STM32 targets (e.g., QEMU bare-metal), define minimal CMSIS macros
#ifndef __NVIC_PRIO_BITS
#define __NVIC_PRIO_BITS 3U  // Cortex-M3/M4 default: 3 bits of priority (8 levels)
#endif

// FPU presence - board config can override via -D__FPU_PRESENT=0 for Cortex-M3
#ifndef __FPU_PRESENT
#define __FPU_PRESENT 1U  // Default: M4 has FPU
#endif

// For non-STM32 targets (QEMU bare-metal), define IRQn_Type enum
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
#endif /* non-STM32 targets */
#endif /* !__ASSEMBLER__ && !_ASMLANGUAGE */

// Core kernel features
#define CONFIG_MULTITHREADING 1
// Note: CONFIG_USE_SWITCH is not supported on ARM Cortex-M
// ARM Cortex-M uses custom main thread switching
#define CONFIG_ARCH_HAS_CUSTOM_SWAP_TO_MAIN 1
#define CONFIG_NUM_PREEMPT_PRIORITIES 15
#define CONFIG_NUM_COOP_PRIORITIES 16
#define CONFIG_MAIN_STACK_SIZE 10240  // 10KB for main thread (reduced to allow more user threads)
#define CONFIG_MAIN_THREAD_PRIORITY 0  // Same priority as user threads for fair k_yield() scheduling
#define CONFIG_IDLE_STACK_SIZE 512
#define CONFIG_ISR_STACK_SIZE 2048
#define CONFIG_THREAD_STACK_INFO 1
#define CONFIG_KERNEL_LOG_LEVEL 0

// Thread configuration
#define CONFIG_THREAD_CUSTOM_DATA 1
#define CONFIG_THREAD_NAME 1
#define CONFIG_THREAD_MAX_NAME_LEN 32
#define CONFIG_THREAD_MONITOR 1  // Required for k_thread_foreach() to work
#define CONFIG_THREAD_STACK_INFO 1  // Track stack info for overflow detection
// CONFIG_THREAD_STACK_SENTINEL disabled to match ports/zephyr (testing hypothesis)
#undef CONFIG_THREAD_STACK_SENTINEL
// CONFIG_DYNAMIC_THREAD commented out to match ports/zephyr thread.conf
#undef CONFIG_DYNAMIC_THREAD

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

// ===========================================================================
// Preemptive Threading Configuration
// ===========================================================================
//
// Zephyr threading uses preemptive multitasking with timeslicing. The following
// configuration ensures proper thread scheduling:
//
// 1. CONFIG_TIMESLICING=1: Enables time-based preemption between equal-priority
//    threads. Without this, threads would only switch on explicit yields or
//    when blocking on synchronization primitives.
//
// 2. CONFIG_TIMESLICE_SIZE: Time in ms before the scheduler preempts a thread.
//    20ms is a reasonable default that balances responsiveness with overhead.
//
// 3. CONFIG_MAIN_THREAD_PRIORITY=0 (line 127): The main thread runs at priority 0.
//    User threads created by mp_thread_create() also use priority 0. This ensures
//    k_yield() works correctly - threads only yield to equal or higher priority.
//
// 4. The GIL (Global Interpreter Lock) controls Python bytecode execution:
//    - Only one thread executes Python at a time (GIL holder)
//    - GIL is released periodically via MICROPY_PY_THREAD_GIL_VM_DIVISOR
//    - When GIL is released (MP_THREAD_GIL_EXIT), we call k_yield() to allow
//      other threads to acquire the GIL and run
//    - Without k_yield() after GIL release, the same thread immediately
//      re-acquires the GIL before others can contend for it
//
// ===========================================================================

// Timeslicing - REQUIRED for preemptive multitasking
#define CONFIG_TIMESLICING 1
#define CONFIG_TIMESLICE_SIZE 20  // 20ms time slices
#define CONFIG_TIMESLICE_PRIORITY 0

// Memory and heap
#define CONFIG_KERNEL_MEM_POOL 0
#define CONFIG_HEAP_MEM_POOL_SIZE 0
#define CONFIG_HEAP_MEM_POOL_IGNORE_MIN 1

// CRITICAL DISABLES - These eliminate generated header dependencies
// Must use #undef (not =0) because kernel code uses #ifdef checks
#undef CONFIG_USERSPACE                     // Ensure it's not defined - disables syscall system
#undef CONFIG_MMU                           // Memory Management Unit (kernel uses #ifdef)
#undef CONFIG_DEMAND_PAGING                 // Demand paging support (kernel uses #ifdef)
#undef CONFIG_DEMAND_PAGING_STATS           // Paging statistics (kernel uses #ifdef)
#define CONFIG_MEM_ATTR 0
#define CONFIG_TIMER_READS_ITS_FREQUENCY_AT_RUNTIME 0  // Disables time_units syscalls

// Userspace-related configs (not used but headers reference them)
#define CONFIG_MAX_DOMAIN_PARTITIONS 0
#define CONFIG_MAX_THREAD_BYTES 0

// Device and device tree - NOT NEEDED for pure threading
// Must use #undef (not =0) because kernel code uses #ifdef checks
#undef CONFIG_DEVICE_DT_METADATA            // Device metadata (kernel uses #ifdef)
#undef CONFIG_DEVICE_DEPS                   // Device dependencies (kernel uses #ifdef)

// Object core (for introspection) - disabled for minimal footprint
// Must use #undef (not =0) because kernel code uses #ifdef checks
#undef CONFIG_OBJ_CORE                      // Object core system (thread.c uses #ifdef)
#undef CONFIG_OBJ_CORE_THREAD               // Thread objects (thread.c uses #ifdef)
#undef CONFIG_OBJ_CORE_STATS                // Object statistics (thread.c uses #ifdef)
#undef CONFIG_OBJ_CORE_STATS_THREAD         // Thread statistics (thread.c uses #ifdef)

// Synchronization primitives
// CONFIG_POLL enables poll_events field in k_sem (changes size from 16 to 32 bytes)
// Disabled to keep minimal Zephyr integration (avoids work queue dependencies)
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
// Must use #undef (not =0) because kernel code uses #ifdef checks
#undef CONFIG_LOG                           // Logging system (kernel uses #ifdef)
#define CONFIG_LOG_MODE_MINIMAL 1
#define CONFIG_ASSERT 1
#undef CONFIG_SPIN_VALIDATE                 // Spinlock validation (kernel uses #ifdef)
#undef CONFIG_ARCH_HAS_THREAD_NAME_HOOK

// Boot arguments
// Must use #undef (not =0) because kernel code uses #ifdef checks
#undef CONFIG_BOOTARGS                      // Boot argument support (init.c uses #ifdef)

// Thread usage monitoring
#define CONFIG_SCHED_THREAD_USAGE 1
#define CONFIG_SCHED_THREAD_USAGE_ALL 0

// FPU support - conditional on __FPU_PRESENT (Cortex-M4 has FPU, M3 does not)
#if __FPU_PRESENT
#define CONFIG_FPU 1
#define CONFIG_FPU_SHARING 1
#else
#undef CONFIG_FPU
#undef CONFIG_FPU_SHARING
#endif

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
// Note: CONFIG_DEVICE_DT_METADATA and CONFIG_DEVICE_DEPS already defined above (lines 196-197)

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

// Cortex-M variant selection based on FPU presence
// Cortex-M4/M4F/M7 have FPU, Cortex-M3 does not
#if __FPU_PRESENT
#define CONFIG_CPU_CORTEX_M4 1
#undef CONFIG_CPU_CORTEX_M3
#else
#define CONFIG_CPU_CORTEX_M3 1
#undef CONFIG_CPU_CORTEX_M4
#endif

#define CONFIG_ARMV7_M_ARMV8_M_MAINLINE 1  // Cortex-M3/M4/M7/M33
#define CONFIG_ARCH "arm"
#define CONFIG_ASSEMBLER_ISA_THUMB2 1  // Cortex-M uses Thumb-2 instruction set

// ARMv7-M (M3/M4/M7) has programmable fault exception priorities
// This enables _EXCEPTION_RESERVED_PRIO=1, making _EXC_IRQ_DEFAULT_PRIO=0x10 instead of 0x00
// Without this, arch_irq_lock() sets BASEPRI=0x00 which disables all masking
#define CONFIG_CPU_CORTEX_M_HAS_PROGRAMMABLE_FAULT_PRIOS 1

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

// NVIC priority bits - required by Zephyr's swap_helper.S for BASEPRI calculations
// MPS2-AN385 CMSDK has 3 priority bits (8 priority levels)
// STM32F4 has 4 priority bits (16 priority levels)
// The port's mpconfigboard should override this if needed
#ifndef NUM_IRQ_PRIO_BITS
#define NUM_IRQ_PRIO_BITS 3
#endif
#define CONFIG_SW_ISR_TABLE 1
#define CONFIG_SW_ISR_TABLE_DYNAMIC 1
#define CONFIG_GEN_ISR_TABLES 1
#define CONFIG_GEN_IRQ_VECTOR_TABLE 1

// ARM exception configuration
// Must use #undef (not #define 0) because Zephyr uses #if defined() checks
#undef CONFIG_ARM_SECURE_FIRMWARE
#undef CONFIG_ARM_NONSECURE_FIRMWARE
// CRITICAL: Store EXC_RETURN value in thread structure for proper context switching
// Without this, mode_exc_return field is uninitialized, causing garbage LR values
// during PendSV and resulting in HardFault lockup
#define CONFIG_ARM_STORE_EXC_RETURN 1
#define CONFIG_GEN_SW_ISR_TABLE 1

// ARM FP configuration - conditional on __FPU_PRESENT
#if __FPU_PRESENT
#define CONFIG_CPU_HAS_FPU 1
#define CONFIG_FP_HARDABI 1
#define CONFIG_FP_SOFTABI 0
#else
#undef CONFIG_CPU_HAS_FPU
#define CONFIG_FP_HARDABI 0
#define CONFIG_FP_SOFTABI 1
#endif

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
