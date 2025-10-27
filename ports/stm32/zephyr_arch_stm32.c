/*
 * This file is part of the MicroPython project, http://micropython.org/
 *
 * The MIT License (MIT)
 *
 * Copyright (c) 2025 MicroPython Developers
 *
 * Zephyr Kernel Architecture Layer for STM32 ARM Cortex-M
 *
 * This file provides the architecture-specific functions required by
 * the Zephyr kernel when running on STM32 microcontrollers.
 *
 * This implementation uses STM32's proven HAL/CMSIS includes and system
 * initialization code.
 */

#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <string.h>
#include <stdbool.h>

#include "py/runtime.h"
#include "py/mphal.h"

// STM32 HAL headers - use port's proven includes
#include "irq.h"

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

// ARM Cortex-M System Control Block (SCB) register addresses
// Use CMSIS definitions from STM32 headers
#define SCB_ICSR_ADDR       (&(SCB->ICSR))
#define SCB_ICSR_PENDSVSET  SCB_ICSR_PENDSVSET_Msk

// SysTick register addresses - use CMSIS macros
#define SYST_CSR_ADDR       (&(SysTick->CTRL))
#define SYST_RVR_ADDR       (&(SysTick->LOAD))
#define SYST_CVR_ADDR       (&(SysTick->VAL))
// SysTick CSR bit definitions
#define SYST_CSR_ENABLE     SysTick_CTRL_ENABLE_Msk
#define SYST_CSR_TICKINT    SysTick_CTRL_TICKINT_Msk
#define SYST_CSR_CLKSOURCE  SysTick_CTRL_CLKSOURCE_Msk

// System Handler Priority Register 3 (for PendSV priority)
#define SCB_SHPR3_ADDR      (&(SCB->SHP[10]))  // PendSV is system handler 14, SHP[10] = (14-4)

// Calculate SysTick reload value
// SysTick is a 24-bit down-counter, reload = (CPU_FREQ / TICK_FREQ) - 1
// Use STM32's SystemCoreClock variable (updated by HAL after clock config)
extern uint32_t SystemCoreClock;

static inline uint32_t get_systick_reload_value(void) {
    return (SystemCoreClock / CONFIG_SYS_CLOCK_TICKS_PER_SEC) - 1;
}

// Global kernel state (normally provided by kernel/init.c)
// For bare-metal, we provide it here
struct z_kernel _kernel __attribute__((section(".bss")));

// Global state for Cortex-M Zephyr arch layer
static struct {
    uint64_t ticks;             // Tick counter (incremented by SysTick)
    int initialized;
} cortexm_arch_state = {0};

// Weak stdio stubs for bare-metal threading support
// These allow py/modthread.c debug output to link, but can be overridden by HAL/libc if available
__attribute__((weak)) struct _reent *_impure_ptr = NULL;

__attribute__((weak)) int fputs(const char *s, FILE *stream) {
    (void)stream;
    // Output to MicroPython's stdout
    while (*s) {
        mp_hal_stdout_tx_strn(s, 1);
        s++;
    }
    return 0;
}

__attribute__((weak)) int fprintf(FILE *stream, const char *format, ...) {
    (void)stream;
    (void)format;
    // Minimal implementation - just suppress the output
    return 0;
}

__attribute__((weak)) size_t fwrite(const void *ptr, size_t size, size_t nmemb, FILE *stream) {
    (void)stream;
    // Output to MicroPython's stdout
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

    #if defined(CONFIG_FPU)
    // FPU initialization (from Zephyr's z_arm_floating_point_init)
    // Clear CPACR first
    SCB->CPACR &= (~(CPACR_CP10_Msk | CPACR_CP11_Msk));
    // Enable CP10 and CP11 for FPU access (full access for both privileged and unprivileged)
    // CPACR bits [21:20] = CP10, bits [23:22] = CP11: 11b = full access, 01b = privileged only
    SCB->CPACR |= CPACR_CP10_FULL_ACCESS | CPACR_CP11_FULL_ACCESS;

    #if defined(CONFIG_FPU_SHARING)
    // FP register sharing mode: enable automatic and lazy state preservation
    // ASPEN: Enable automatic FP context save on exception entry
    // LSPEN: Enable lazy FP context save (only save if FP instructions used)
    FPU->FPCCR = FPU_FPCCR_ASPEN_Msk | FPU_FPCCR_LSPEN_Msk;
    #else
    // Unshared mode: disable automatic stacking
    FPU->FPCCR &= (~(FPU_FPCCR_ASPEN_Msk | FPU_FPCCR_LSPEN_Msk));
    #endif

    // Memory barriers to ensure CPACR and FPCCR changes take effect
    __DMB();
    __ISB();

    // Initialize FPSCR (Floating Point Status and Control Register)
    __set_FPSCR(0);

    // Instruction barrier to ensure FPSCR initialization completes before any FP operations
    __ISB();
    #endif

    // Configure SysTick for CONFIG_SYS_CLOCK_TICKS_PER_SEC (1000 Hz = 1ms ticks)
    // Reload value calculated from SystemCoreClock (updated by HAL)

    // Configure SysTick (ARMv7-M System Control Block)
    *SYST_CSR_ADDR = 0;  // Disable SysTick first
    *SYST_RVR_ADDR = get_systick_reload_value();  // Set reload value
    *SYST_CVR_ADDR = 0;  // Clear current value
    // NOTE: Do NOT enable SysTick interrupt yet - wait until after kernel init
    // Enable counter with processor clock source, but NO interrupt yet
    *SYST_CSR_ADDR = SYST_CSR_ENABLE | SYST_CSR_CLKSOURCE;  // No TICKINT bit

    // Set PendSV to lowest priority (for context switching)
    *SCB_SHPR3_ADDR = 0xFF;  // PendSV priority = 0xFF (lowest)

    cortexm_arch_state.initialized = 1;

    // NOTE: Cannot use mp_printf here - stdio not initialized yet
    // mp_printf(&mp_plat_print, "Zephyr arch (STM32 Cortex-M): Initialized\n");
}

// Enable SysTick interrupt - must be called AFTER kernel is fully initialized
// This should be called from micropython_main_thread_entry() after z_cstart() completes
void mp_zephyr_arch_enable_systick_interrupt(void) {
    // Enable SysTick interrupt (add TICKINT bit to existing configuration)
    *SYST_CSR_ADDR = SYST_CSR_ENABLE | SYST_CSR_CLKSOURCE | SYST_CSR_TICKINT;
}

// Get current system tick count
uint64_t mp_zephyr_arch_get_ticks(void) {
    return cortexm_arch_state.ticks;
}

// Trigger a context switch (yield to scheduler)
// On Cortex-M, we use PendSV for context switching
void mp_zephyr_arch_yield(void) {
    // Set PendSV interrupt pending bit in ICSR register
    *SCB_ICSR_ADDR = SCB_ICSR_PENDSVSET;
}

// Zephyr systick handler - called by port's SysTick_Handler when MICROPY_ZEPHYR_THREADING enabled
// This function handles Zephyr-specific tick counting and scheduling
void mp_zephyr_systick_handler(void) {
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
    if (_kernel.ready_q.cache != NULL &&
        _kernel.ready_q.cache != _kernel.cpus[0].current) {
        // Trigger PendSV for context switch
        *SCB_ICSR_ADDR = SCB_ICSR_PENDSVSET;
    }
}

// Zephyr kernel deinitialization
void mp_zephyr_kernel_deinit(void) {
    // Cleanup if needed
    // NOTE: This may be called before stdio is fully ready, be careful with prints
    // mp_printf(&mp_plat_print, "Zephyr kernel deinitialized (STM32 Cortex-M mode)\n");
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

// SMP (multi-core) stubs - single-core implementation
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

// Start a newly created thread (called from mpthread)
// Threads are created with K_NO_WAIT so they're already in the ready queue
void mp_zephyr_thread_start(struct k_thread *thread) {
    // Thread is already in ready queue (created with K_NO_WAIT)
    // Just trigger PendSV to force a context switch
    // This will cause the scheduler to run and switch to the new thread if it has higher priority
    (void)thread;  // Unused - thread is already ready
    *SCB_ICSR_ADDR = SCB_ICSR_PENDSVSET;
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
