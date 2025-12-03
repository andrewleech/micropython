/*
 * This file is part of the MicroPython project, http://micropython.org/
 *
 * The MIT License (MIT)
 *
 * Copyright (c) 2025 MicroPython Developers
 *
 * Zephyr Kernel Configuration for MicroPython Threading
 *
 * This file provides fixed CONFIG_ definitions to allow compiling
 * the Zephyr kernel without the full Zephyr build system (west/Kconfig).
 */

#ifndef MICROPY_INCLUDED_EXTMOD_ZEPHYR_KERNEL_CONFIG_H
#define MICROPY_INCLUDED_EXTMOD_ZEPHYR_KERNEL_CONFIG_H

// Include devicetree fixup BEFORE any CONFIG defines
// This must be first to stub out problematic DT macros
#include "zephyr/devicetree_fixup.h"

// Core kernel features
#define CONFIG_MULTITHREADING 1
#define CONFIG_NUM_PREEMPT_PRIORITIES 15
#define CONFIG_NUM_COOP_PRIORITIES 16
#define CONFIG_MAIN_STACK_SIZE 8192  // 8KB (POSIX arch uses pthread stack anyway)
#define CONFIG_MAIN_THREAD_PRIORITY 0
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

// TODO: Enable CONFIG_CURRENT_THREAD_USE_TLS to avoid races with _current global
// Currently causes hangs during initialization - needs investigation
// #define CONFIG_CURRENT_THREAD_USE_TLS 1

// Scheduler configuration
#define CONFIG_SCHED_DUMB 0
#define CONFIG_SCHED_SCALABLE 1
#define CONFIG_SCHED_MULTIQ 0
#define CONFIG_WAITQ_SCALABLE 1
#define CONFIG_WAITQ_DUMB 0
#define CONFIG_SCHED_CPU_MASK 0

// SMP configuration (disabled - must be undefined for single-core)
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
#define CONFIG_TIMESLICE_SIZE 0
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
#define CONFIG_POLL 1
#define CONFIG_EVENTS 1

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
#define CONFIG_SCHED_THREAD_USAGE 0
#define CONFIG_SCHED_THREAD_USAGE_ALL 0

// FPU support
#define CONFIG_FPU 0
#define CONFIG_FPU_SHARING 0

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

// Architecture configuration
// For Unix port with Zephyr threading, use POSIX architecture
#ifdef MICROPY_ZEPHYR_THREADING
// Force POSIX architecture for Unix/hosted simulation
#define CONFIG_POSIX 1
#define CONFIG_ARCH_POSIX 1
#define CONFIG_ARCH "posix"
#define CONFIG_64BIT 1

// POSIX architecture configuration
#define CONFIG_ARCH_HAS_CUSTOM_BUSY_WAIT 1
#define CONFIG_ARCH_HAS_THREAD_ABORT 1
#define CONFIG_ARCH_HAS_SUSPEND_TO_RAM 0

// POSIX-specific options
#define CONFIG_PRIVILEGED_STACK_SIZE 1024
#define CONFIG_MMU_PAGE_SIZE 4096
#define CONFIG_MAX_IRQ_LINES 128
#define CONFIG_KERNEL_VM_BASE 0
#define CONFIG_KERNEL_VM_OFFSET 0
#define CONFIG_SRAM_BASE_ADDRESS 0
#define CONFIG_SRAM_OFFSET 0
#else
// Standard architecture detection for non-Zephyr builds
#if defined(__x86_64__)
#define CONFIG_X86 1
#define CONFIG_X86_64 1
#define CONFIG_64BIT 1
#elif defined(__i386__)
#define CONFIG_X86 1
#elif defined(__arm__) || defined(__thumb__)
#define CONFIG_ARM 1
#define CONFIG_CPU_CORTEX_M 1
#elif defined(__riscv)
#define CONFIG_RISCV 1
#else
#error "Unsupported architecture for Zephyr kernel integration"
#endif
#endif

// Ensure we're not in unit test mode (so __syscall becomes "static inline")
#ifdef ZTEST_UNITTEST
#undef ZTEST_UNITTEST
#endif

// Z_SYSCALL macros - stub out userspace validation
#define Z_SYSCALL_DECLARE(...)
#define Z_SYSCALL_HANDLER(...)

#endif // MICROPY_INCLUDED_EXTMOD_ZEPHYR_KERNEL_CONFIG_H
