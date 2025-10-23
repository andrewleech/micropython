/*
 * This file is part of the MicroPython project, http://micropython.org/
 *
 * The MIT License (MIT)
 *
 * Copyright (c) 2025 MicroPython Developers
 *
 * Zephyr Kernel Architecture Layer for ARM Cortex-M
 *
 * This file provides the architecture-specific functions required by
 * the Zephyr kernel when running on bare-metal ARM Cortex-M systems
 * (e.g., QEMU mps2-an385, STM32, nRF52).
 */

#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <string.h>

#if MICROPY_ZEPHYR_THREADING

#include "py/runtime.h"
#include "extmod/zephyr_kernel/zephyr_kernel.h"
#include <zephyr/kernel.h>
#include <zephyr/arch/cpu.h>

// Global kernel state (normally provided by kernel/init.c)
// For bare-metal, we provide it here
struct z_kernel _kernel __attribute__((section(".bss")));

// Global state for Cortex-M Zephyr arch layer
static struct {
    uint64_t ticks;             // Tick counter (incremented by SysTick)
    int initialized;
} cortexm_arch_state = {0};

// Initialize architecture-specific components
void mp_zephyr_arch_init(void) {
    if (cortexm_arch_state.initialized) {
        return;
    }

    // Initialize tick counter
    cortexm_arch_state.ticks = 0;

    // Configure SysTick for 1ms ticks (1000 Hz = CONFIG_SYS_CLOCK_TICKS_PER_SEC)
    // This assumes a 25MHz CPU clock (typical for QEMU mps2-an385)
    // SysTick reload = (CPU_FREQ / TICKS_PER_SEC) - 1
    // 25000000 / 1000 - 1 = 24999
    uint32_t reload = 24999;  // For 25MHz CPU @ 1kHz tick rate

    // Configure SysTick (ARMv7-M System Control Block)
    // SysTick Control and Status Register
    *(volatile uint32_t *)0xE000E010 = 0;  // Disable SysTick first
    // SysTick Reload Value Register
    *(volatile uint32_t *)0xE000E014 = reload;
    // SysTick Current Value Register
    *(volatile uint32_t *)0xE000E018 = 0;
    // SysTick Control and Status Register: enable, enable interrupt, use processor clock
    *(volatile uint32_t *)0xE000E010 = 0x07;

    // Set PendSV to lowest priority (for context switching)
    // SHPR3 (System Handler Priority Register 3) at 0xE000ED20
    *(volatile uint32_t *)0xE000ED20 |= 0xFF000000;  // PendSV priority = 0xFF (lowest)

    cortexm_arch_state.initialized = 1;

    mp_printf(&mp_plat_print, "Zephyr arch (Cortex-M): Initialized\n");
}

// Get current system tick count
uint64_t mp_zephyr_arch_get_ticks(void) {
    return cortexm_arch_state.ticks;
}

// Trigger a context switch (yield to scheduler)
// On Cortex-M, we use PendSV for context switching
void mp_zephyr_arch_yield(void) {
    // Set PendSV interrupt pending bit
    // ICSR (Interrupt Control and State Register) at 0xE000ED04
    *(volatile uint32_t *)0xE000ED04 = (1 << 28);  // PENDSVSET
}

// SysTick interrupt handler - increments tick counter and calls Zephyr timer
void SysTick_Handler(void) {
    cortexm_arch_state.ticks++;

    // Call Zephyr clock announce (if available)
    extern void z_clock_announce(int32_t ticks);
    z_clock_announce(1);
}

// PendSV interrupt handler - performs context switching
void PendSV_Handler(void) {
    // Call Zephyr's PendSV handler
    extern void z_arm_pendsv(void);
    z_arm_pendsv();
}

// Bootstrap thread structure for the main thread
static struct k_thread bootstrap_thread;
static int kernel_initialized = 0;

// Zephyr kernel initialization for Cortex-M
void mp_zephyr_kernel_init(void *main_stack, uint32_t main_stack_len) {
    // Make this function idempotent - safe to call multiple times
    if (kernel_initialized) {
        return;
    }

    (void)main_stack;      // For now, use existing stack
    (void)main_stack_len;

    // Initialize arch-specific components (SysTick, PendSV, etc.)
    mp_zephyr_arch_init();

    // Zero out the kernel structure
    memset(&_kernel, 0, sizeof(_kernel));

    // Initialize the scheduler and ready queue
    extern void z_sched_init(void);
    z_sched_init();

    // Set up a minimal bootstrap thread for the main thread
    // This is needed so that k_thread_create() has a valid _current to copy from
    memset(&bootstrap_thread, 0, sizeof(bootstrap_thread));

    // Set this bootstrap thread as the current thread
    _kernel.cpus[0].current = &bootstrap_thread;

    kernel_initialized = 1;

    mp_printf(&mp_plat_print, "Zephyr kernel initialized (Cortex-M threading mode)\n");
}

// Zephyr kernel deinitialization
void mp_zephyr_kernel_deinit(void) {
    // Cleanup if needed
    mp_printf(&mp_plat_print, "Zephyr kernel deinitialized (Cortex-M mode)\n");
}

// ============================================================================
// Zephyr Architecture Stubs for Cortex-M
// ============================================================================

// System clock elapsed time (stub)
uint32_t sys_clock_elapsed(void) {
    // Return 0 for basic implementation (not using tickless idle)
    return 0;
}

// Spinlock functions (not needed on single-core)
void arch_spin_relax(void) {
    // No-op on single-core
}

void z_spin_lock_set_owner(struct k_spinlock *l) {
    (void)l;
    // No-op on single-core
}

// System clock timeout (stub - not used yet)
void sys_clock_set_timeout(k_ticks_t ticks, bool idle) {
    (void)ticks;
    (void)idle;
    // No-op for now
}

// SMP (multi-core) stubs - Cortex-M3 is single-core
struct k_thread *z_smp_current_get(void) {
    // Return NULL for single-core
    return NULL;
}

// Object core stubs (statistics/tracing - not needed)
void k_obj_core_init_and_link(struct k_obj_core *obj_core, struct k_obj_type *type) {
    (void)obj_core;
    (void)type;
}

int k_obj_core_stats_register(struct k_obj_core *obj_core, void *stats, size_t stats_len) {
    (void)obj_core;
    (void)stats;
    (void)stats_len;
    return 0;
}

int k_obj_core_stats_deregister(struct k_obj_core *obj_core) {
    (void)obj_core;
    return 0;
}

void k_obj_core_unlink(struct k_obj_core *obj_core) {
    (void)obj_core;
}

void *k_mem_map_phys_guard(uintptr_t phys, size_t size, uint32_t flags, bool is_anon) {
    (void)phys;
    (void)size;
    (void)flags;
    (void)is_anon;
    return NULL;
}

// Scheduler lock/unlock (use simple critical sections for single-core)
void z_sched_lock(void) {
    // Disable interrupts for critical section
    arch_irq_lock();
}

void z_sched_unlock(void) {
    // Re-enable interrupts
    arch_irq_unlock(1);
}

// Check if in ISR context
bool arch_is_in_isr(void) {
    // Read IPSR (Interrupt Program Status Register) - bits 0-8
    // If IPSR != 0, we're in an exception handler
    uint32_t ipsr;
    __asm__ volatile ("mrs %0, ipsr" : "=r" (ipsr));
    return (ipsr & 0x1FF) != 0;
}

// Idle thread check
bool z_is_idle_thread_object(void *obj) {
    // For now, assume no idle thread
    return false;
}

// Console output function
void k_str_out(char *c, size_t n) {
    // Use MicroPython's print function
    for (size_t i = 0; i < n; i++) {
        mp_hal_stdout_tx_strn(c + i, 1);
    }
}

// Printk hook stub
void __printk_hook_install(int (*fn)(int)) {
    (void)fn;
}

// Fatal error handler
void z_fatal_error(unsigned int reason, const struct arch_esf *esf) {
    (void)esf;
    mp_printf(&mp_plat_print, "Zephyr fatal error: reason=%u\n", reason);
    for (;;) {
        // Halt
    }
}

// Time slice reset stub
void z_reset_time_slice(struct k_thread *thread) {
    (void)thread;
}

// SMP/IPI stubs - single-core, no IPI needed
uint32_t ipi_mask_create(struct k_thread *thread) {
    (void)thread;
    return 0;
}

void flag_ipi(uint32_t ipi_mask) {
    (void)ipi_mask;
}

void signal_pending_ipi(void) {
    // No-op for single-core
}

// Thread return value (not used on Cortex-M)
void arch_thread_return_value_set(struct k_thread *thread, unsigned int value) {
    (void)thread;
    (void)value;
}

#endif // MICROPY_ZEPHYR_THREADING
