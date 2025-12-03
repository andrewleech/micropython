/*
 * This file is part of the MicroPython project, http://micropython.org/
 *
 * The MIT License (MIT)
 *
 * Copyright (c) 2025 MicroPython Developers
 *
 * Zephyr Kernel Architecture Layer for QEMU ARM Cortex-M (MPS2-AN385)
 *
 * This file provides the architecture-specific functions required by
 * the Zephyr kernel when running on QEMU MPS2-AN385 (Cortex-M3).
 */

#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <string.h>
#include <stdbool.h>

#include "py/runtime.h"
#include "py/mphal.h"

// Provide minimal CONFIG symbols (normally from autoconf.h)
#ifndef CONFIG_SYS_CLOCK_TICKS_PER_SEC
#define CONFIG_SYS_CLOCK_TICKS_PER_SEC 1000
#endif
#ifndef CONFIG_MP_MAX_NUM_CPUS
#define CONFIG_MP_MAX_NUM_CPUS 1
#endif

#if MICROPY_ZEPHYR_THREADING

// Include Zephyr headers
#include <zephyr/kernel.h>
#include <zephyr/kernel_structs.h>
#include <zephyr/arch/cpu.h>
#include "../../lib/zephyr/kernel/include/kthread.h"

// ARM Cortex-M System Control Block (SCB) register addresses
#define SCB_ICSR_ADDR       ((volatile uint32_t *)0xE000ED04)
#define SCB_ICSR_PENDSVSET  (1UL << 28)

// SysTick register addresses (ARMv7-M Architecture Reference Manual B3.3)
#define SYST_CSR_ADDR       ((volatile uint32_t *)0xE000E010)
#define SYST_RVR_ADDR       ((volatile uint32_t *)0xE000E014)
#define SYST_CVR_ADDR       ((volatile uint32_t *)0xE000E018)
// SysTick CSR bit definitions
#define SYST_CSR_ENABLE     (1UL << 0)
#define SYST_CSR_TICKINT    (1UL << 1)
#define SYST_CSR_CLKSOURCE  (1UL << 2)

// System Handler Priority Register 3 (for PendSV priority)
#define SCB_SHPR3_ADDR      ((volatile uint32_t *)0xE000ED20)

// QEMU MPS2-AN385 CPU frequency (hardcoded - no HAL)
#define CPU_FREQ_HZ         25000000u

// Calculate SysTick reload value
static inline uint32_t get_systick_reload_value(void) {
    return (CPU_FREQ_HZ / CONFIG_SYS_CLOCK_TICKS_PER_SEC) - 1;
}

// Global kernel state (normally provided by kernel/init.c)
struct z_kernel _kernel __attribute__((section(".bss")));

// Global state for Cortex-M Zephyr arch layer
static struct {
    uint64_t ticks;
    int initialized;
} cortexm_arch_state = {0};

// Weak stdio stubs for bare-metal threading support
__attribute__((weak)) struct _reent *_impure_ptr = NULL;

__attribute__((weak)) int fputs(const char *s, FILE *stream) {
    (void)stream;
    while (*s) {
        mp_hal_stdout_tx_strn(s, 1);
        s++;
    }
    return 0;
}

__attribute__((weak)) int fprintf(FILE *stream, const char *format, ...) {
    (void)stream;
    (void)format;
    return 0;
}

__attribute__((weak)) size_t fwrite(const void *ptr, size_t size, size_t nmemb, FILE *stream) {
    (void)stream;
    const char *s = (const char *)ptr;
    size_t total = size * nmemb;
    for (size_t i = 0; i < total; i++) {
        mp_hal_stdout_tx_strn(&s[i], 1);
    }
    return nmemb;
}

// Initialize architecture-specific components
void mp_zephyr_arch_init(void) {
    if (cortexm_arch_state.initialized) {
        return;
    }

    // Initialize tick counter
    cortexm_arch_state.ticks = 0;

    // MPS2-AN385 is Cortex-M3 - no FPU initialization needed

    // Configure SysTick for 1000 Hz (1ms ticks)
    // Note: QEMU's ticks_init() also configures SysTick, but we override here
    // to ensure consistent Zephyr timing configuration
    *SYST_CSR_ADDR = 0;  // Disable SysTick first
    *SYST_RVR_ADDR = get_systick_reload_value();
    *SYST_CVR_ADDR = 0;  // Clear current value
    // Enable counter with processor clock source, but NO interrupt yet
    *SYST_CSR_ADDR = SYST_CSR_ENABLE | SYST_CSR_CLKSOURCE;

    // Set PendSV to lowest priority (for context switching)
    *SCB_SHPR3_ADDR |= (0xFFUL << 16);  // PendSV priority = 0xFF (lowest)

    cortexm_arch_state.initialized = 1;
}

// Enable SysTick interrupt - called after kernel is fully initialized
void mp_zephyr_arch_enable_systick_interrupt(void) {
    // Set SysTick priority to 2 (0x20) - must be maskable for critical sections to work
    // SysTick priority is in bits 31:24 of SCB_SHPR3
    // NOTE: On QEMU, SysTick can interrupt PendSV despite BASEPRI=0x20 masking,
    // possibly due to QEMU emulation issues with exception return from nested interrupts
    *SCB_SHPR3_ADDR = (*SCB_SHPR3_ADDR & 0x00FFFFFF) | (0x20UL << 24);
    // Enable SysTick with interrupt
    *SYST_CSR_ADDR = SYST_CSR_ENABLE | SYST_CSR_CLKSOURCE | SYST_CSR_TICKINT;
}

// Get current system tick count
uint64_t mp_zephyr_arch_get_ticks(void) {
    return cortexm_arch_state.ticks;
}

// Trigger a context switch (yield to scheduler)
void mp_zephyr_arch_yield(void) {
    *SCB_ICSR_ADDR = SCB_ICSR_PENDSVSET;
}

// Zephyr kernel deinitialization
void mp_zephyr_kernel_deinit(void) {
    // Cleanup if needed
}

// SysTick interrupt handler - increments tick counter and calls Zephyr timer subsystem
void SysTick_Handler(void) {
    cortexm_arch_state.ticks++;

    // Call port-specific hook to maintain MicroPython _ticks_ms counter
    extern void mp_zephyr_port_systick_hook(void);
    mp_zephyr_port_systick_hook();

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
    if (_kernel.ready_q.cache != NULL &&
        _kernel.ready_q.cache != _kernel.cpus[0].current) {
        // Trigger PendSV for context switch
        *SCB_ICSR_ADDR = SCB_ICSR_PENDSVSET;
    }
}

// NOTE: PendSV_Handler is defined in errorhandler.c
// It jumps to z_arm_pendsv when MICROPY_ZEPHYR_THREADING is enabled

// ============================================================================
// Zephyr Architecture Stubs for Cortex-M
// ============================================================================

uint32_t sys_clock_elapsed(void) {
    return 0;
}

void arch_spin_relax(void) {
}

void z_spin_lock_set_owner(struct k_spinlock *l) {
    (void)l;
}

void sys_clock_set_timeout(k_ticks_t ticks, bool idle) {
    (void)ticks;
    (void)idle;
}

struct k_thread *z_smp_current_get(void) {
    return NULL;
}

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

void z_sched_lock(void) {
    arch_irq_lock();
}

void z_sched_unlock(void) {
    arch_irq_unlock(1);
}

void k_str_out(char *c, size_t n) {
    for (size_t i = 0; i < n; i++) {
        mp_hal_stdout_tx_strn(c + i, 1);
    }
}

void __printk_hook_install(int (*fn)(int)) {
    (void)fn;
}

void z_fatal_error(unsigned int reason, const struct arch_esf *esf) {
    (void)esf;
    mp_printf(&mp_plat_print, "Zephyr fatal error: reason=%u\n", reason);
    for (;;) {
    }
}

// Idle thread array stub
#if !defined(MICROPY_ZEPHYR_USE_IDLE_THREAD) || !MICROPY_ZEPHYR_USE_IDLE_THREAD
struct k_thread z_idle_threads[CONFIG_MP_MAX_NUM_CPUS];
#endif

uint32_t ipi_mask_create(struct k_thread *thread) {
    (void)thread;
    return 0;
}

void flag_ipi(uint32_t ipi_mask) {
    (void)ipi_mask;
}

void signal_pending_ipi(void) {
}

#endif // MICROPY_ZEPHYR_THREADING
