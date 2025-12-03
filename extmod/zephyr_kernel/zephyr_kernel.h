/*
 * This file is part of the MicroPython project, http://micropython.org/
 *
 * The MIT License (MIT)
 *
 * Copyright (c) 2025 MicroPython Developers
 *
 * Zephyr Kernel Integration API for MicroPython
 *
 * This header provides the bridge between MicroPython's threading API
 * (mp_thread_*) and the Zephyr kernel primitives.
 */

#ifndef MICROPY_INCLUDED_EXTMOD_ZEPHYR_KERNEL_H
#define MICROPY_INCLUDED_EXTMOD_ZEPHYR_KERNEL_H

// Zephyr configuration is included via command-line -include flag
// (either zephyr_config.h for POSIX or zephyr_config_cortex_m.h for ARM)

// Include Zephyr kernel headers
#include <zephyr/kernel.h>
#include <zephyr/sys/printk.h>

// MicroPython includes
#include "py/mpconfig.h"
#include "py/mpstate.h"

#ifdef __cplusplus
extern "C" {
#endif

// Initialize Zephyr kernel for MicroPython use
// Returns true on success, false on failure
bool mp_zephyr_kernel_init(void);

// Deinitialize Zephyr kernel
void mp_zephyr_kernel_deinit(void);

// Architecture-specific functions that must be provided by each port
// These are implemented in ports/*/zephyr_arch_*.c

// Initialize architecture-specific components (timers, interrupts, etc.)
void mp_zephyr_arch_init(void);

// Get the current system tick count
uint64_t mp_zephyr_arch_get_ticks(void);

// Trigger a context switch (typically via PendSV or similar)
void mp_zephyr_arch_yield(void);

// Atomic operations wrapper (if not using Zephyr's built-in)
#ifndef CONFIG_ATOMIC_OPERATIONS_BUILTIN
// These would be implemented per-architecture if needed
#endif

// Debug/logging helpers
#if CONFIG_LOG
#define MP_ZEPHYR_LOG(...) printk(__VA_ARGS__)
#else
#define MP_ZEPHYR_LOG(...) ((void)0)
#endif

// Note: Thread mutex types are defined in the port's mpthreadport.h

#ifdef __cplusplus
}
#endif

#endif // MICROPY_INCLUDED_EXTMOD_ZEPHYR_KERNEL_H
