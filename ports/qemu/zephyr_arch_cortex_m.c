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
#include "py/mphal.h"
#include "extmod/zephyr_kernel/zephyr_kernel.h"
#include <zephyr/kernel.h>
#include <zephyr/arch/cpu.h>

// Minimal stdio stubs for bare-metal environment (DEBUG_printf support)
// These weak symbols are used when DEBUG_printf is enabled but C library stdio is unavailable
// If C library provides these, those versions will be used instead

// Newlib re-entrancy structure stub
struct _reent;
__attribute__((weak))
struct _reent *_impure_ptr = NULL;

__attribute__((weak))
int fputs(const char *s, FILE *stream) {
    (void)stream;
    // Output to MicroPython's stdout
    while (*s) {
        mp_hal_stdout_tx_strn(s, 1);
        s++;
    }
    return 0;
}

__attribute__((weak))
int fprintf(FILE *stream, const char *format, ...) {
    (void)stream;
    (void)format;
    // Minimal implementation - just suppress the output
    // A full implementation would need va_list handling
    return 0;
}

__attribute__((weak))
size_t fwrite(const void *ptr, size_t size, size_t nmemb, FILE *stream) {
    (void)stream;
    // Output to MicroPython's stdout
    const char *s = (const char *)ptr;
    size_t total = size * nmemb;
    for (size_t i = 0; i < total; i++) {
        mp_hal_stdout_tx_strn(&s[i], 1);
    }
    return nmemb;
}

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
    // NOTE: Do NOT enable SysTick interrupt yet - wait until after kernel init
    // SysTick Control and Status Register: enable counter, use processor clock, but NO interrupt yet
    *(volatile uint32_t *)0xE000E010 = 0x05;  // Enable + processor clock, NO interrupt (bit 1 = 0)

    // Set PendSV to lowest priority (for context switching)
    // SHPR3 (System Handler Priority Register 3) at 0xE000ED20
    *(volatile uint32_t *)0xE000ED20 |= 0xFF000000;  // PendSV priority = 0xFF (lowest)

    cortexm_arch_state.initialized = 1;

    // NOTE: Cannot use mp_printf here - stdio not initialized yet
    // mp_printf(&mp_plat_print, "Zephyr arch (Cortex-M): Initialized\n");
}

// Enable SysTick interrupt - must be called AFTER kernel is fully initialized
// This should be called from micropython_main_thread_entry() after z_cstart() completes
void mp_zephyr_arch_enable_systick_interrupt(void) {
    // Enable SysTick interrupt (set bit 1 in SysTick Control register)
    // SysTick Control and Status Register: enable counter + processor clock + interrupt
    *(volatile uint32_t *)0xE000E010 = 0x07;  // Enable + processor clock + interrupt
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

// SysTick interrupt handler - increments tick counter and calls Zephyr timer subsystem
void SysTick_Handler(void) {
    cortexm_arch_state.ticks++;

    // Call Zephyr's timer subsystem to process timeouts and trigger scheduling
    // sys_clock_announce() will:
    // - Process expired timeouts from timeout_list
    // - Call timeout callback functions (including thread wakeup via z_ready_thread())
    // - Update curr_tick
    // - Call z_time_slice() if CONFIG_TIMESLICING is enabled
    extern void sys_clock_announce(int32_t ticks);
    sys_clock_announce(1);

    // After processing timeouts, check if we need to reschedule
    // sys_clock_announce() may have woken up threads via timeout callbacks,
    // but it doesn't automatically trigger a context switch. We need to check
    // if a higher-priority thread is ready and trigger PendSV if needed.
    extern struct z_kernel _kernel;
    if (_kernel.ready_q.cache != NULL &&
        _kernel.ready_q.cache != _kernel.cpus[0].current) {
        // Trigger PendSV for context switch
        *(volatile uint32_t *)0xE000ED04 = (1 << 28);  // PENDSVSET
    }
}

// PendSV interrupt handler - performs context switching
void PendSV_Handler(void) {
    // Call Zephyr's PendSV handler
    extern void z_arm_pendsv(void);
    z_arm_pendsv();
}

// NOTE: With the new z_cstart() approach, most kernel initialization
// is now done in extmod/zephyr_kernel/zephyr_cstart.c
// This function is kept for compatibility but is now minimal

// Zephyr kernel deinitialization
void mp_zephyr_kernel_deinit(void) {
    // Cleanup if needed
    // NOTE: This may be called before stdio is fully ready, be careful with prints
    // mp_printf(&mp_plat_print, "Zephyr kernel deinitialized (Cortex-M mode)\n");
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

// Idle thread array stub (normally defined in init.c)
// We don't use an idle thread in our minimal implementation, but timeslicing.c
// needs this to exist for z_is_idle_thread_object() to work.
struct k_thread z_idle_threads[CONFIG_MP_MAX_NUM_CPUS];

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

// Thread return value - provided by arch layer for legacy swap (not CONFIG_USE_SWITCH)
// Note: Cortex-M's kernel_arch_func.h provides this as static inline,
// but we need a non-inline version for linking in some contexts
void arch_thread_return_value_set(struct k_thread *thread, unsigned int value) {
    thread->arch.swap_return_value = value;
}

#endif // MICROPY_ZEPHYR_THREADING
