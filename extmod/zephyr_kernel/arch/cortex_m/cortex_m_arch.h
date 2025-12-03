/*
 * This file is part of the MicroPython project, http://micropython.org/
 *
 * The MIT License (MIT)
 *
 * Copyright (c) 2025 MicroPython Developers
 *
 * Zephyr Kernel Architecture Layer for ARM Cortex-M - Public Interface
 *
 * This header defines the interface between the Cortex-M architecture layer
 * and MicroPython ports using Zephyr threading.
 */

#ifndef EXTMOD_ZEPHYR_KERNEL_CORTEX_M_ARCH_H
#define EXTMOD_ZEPHYR_KERNEL_CORTEX_M_ARCH_H

#include <stdint.h>

// Initialize architecture-specific components (FPU, SysTick, PendSV)
// Must be called before z_cstart() is invoked
void mp_zephyr_arch_init(void);

// Enable SysTick interrupt - must be called AFTER kernel is fully initialized
// This should be called from micropython_main_thread_entry() after z_cstart() completes
void mp_zephyr_arch_enable_systick_interrupt(void);

// Get current system tick count
uint64_t mp_zephyr_arch_get_ticks(void);

// Trigger a context switch (yield to scheduler)
void mp_zephyr_arch_yield(void);

// Zephyr kernel deinitialization (cleanup on shutdown)
void mp_zephyr_kernel_deinit(void);

#endif // EXTMOD_ZEPHYR_KERNEL_CORTEX_M_ARCH_H
