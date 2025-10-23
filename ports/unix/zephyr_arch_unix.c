/*
 * This file is part of the MicroPython project, http://micropython.org/
 *
 * The MIT License (MIT)
 *
 * Copyright (c) 2025 MicroPython Developers
 *
 * Zephyr Kernel Architecture Layer for Unix/POSIX
 *
 * This file provides the architecture-specific functions required by
 * the Zephyr kernel when running on Unix/POSIX systems. For the POC,
 * this uses pthread as a backing implementation.
 */

#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <string.h>
#include <pthread.h>
#include <time.h>
#include <sys/time.h>
#include <unistd.h>

#include "extmod/zephyr_kernel/zephyr_kernel.h"
#include <posix_core.h>

#if MICROPY_ZEPHYR_THREADING

// Global kernel state (normally provided by kernel/init.c)
// For Unix POC, we provide it here without __pinned_bss attribute
struct z_kernel _kernel;

// Global state for Unix Zephyr arch layer
static struct {
    uint64_t boot_time_us;      // Boot time in microseconds
    pthread_mutex_t time_lock;  // Protect time calculations
    int initialized;
} unix_arch_state = {0};

// Initialize architecture-specific components
void mp_zephyr_arch_init(void) {
    if (unix_arch_state.initialized) {
        return;
    }

    // Record boot time
    struct timeval tv;
    gettimeofday(&tv, NULL);
    unix_arch_state.boot_time_us = (uint64_t)tv.tv_sec * 1000000ULL + tv.tv_usec;

    // Initialize locks
    pthread_mutex_init(&unix_arch_state.time_lock, NULL);

    unix_arch_state.initialized = 1;

    fprintf(stderr, "Zephyr arch (Unix): Initialized\n");
}

// Get current system tick count
// Zephyr expects ticks at CONFIG_SYS_CLOCK_TICKS_PER_SEC rate (1000 Hz = 1ms)
uint64_t mp_zephyr_arch_get_ticks(void) {
    struct timeval tv;
    gettimeofday(&tv, NULL);

    uint64_t now_us = (uint64_t)tv.tv_sec * 1000000ULL + tv.tv_usec;
    uint64_t elapsed_us = now_us - unix_arch_state.boot_time_us;

    // Convert to ticks (1000 ticks/sec = 1 tick per millisecond)
    return elapsed_us / 1000;
}

// Trigger a context switch (yield to scheduler)
// On Unix with pthreads, we can use sched_yield()
void mp_zephyr_arch_yield(void) {
    sched_yield();
}

// Bootstrap thread structure for the process main thread (Thread 1)
// This provides a valid _current for the initial k_thread_create() call
static struct k_thread bootstrap_thread;
static posix_thread_status_t bootstrap_thread_status;
static int kernel_initialized = 0;

// Zephyr kernel initialization for Unix
void mp_zephyr_kernel_init(void *main_stack, uint32_t main_stack_len) {
    // Make this function idempotent - safe to call multiple times
    if (kernel_initialized) {
        return;
    }

    (void)main_stack;      // Unix/Zephyr manages its own stacks
    (void)main_stack_len;

    // Initialize arch-specific components (timers, etc.)
    mp_zephyr_arch_init();

    // Zero out the kernel structure
    memset(&_kernel, 0, sizeof(_kernel));

    // Initialize the scheduler and ready queue
    extern void z_sched_init(void);
    z_sched_init();

    // Initialize the POSIX architecture threading layer
    // This registers the calling pthread as thread 0 (the process main thread)
    posix_arch_init();

    // Set up a minimal bootstrap thread for Thread 1 (the process main thread)
    // This is needed so that k_thread_create() has a valid _current to copy from
    memset(&bootstrap_thread, 0, sizeof(bootstrap_thread));
    memset(&bootstrap_thread_status, 0, sizeof(bootstrap_thread_status));

    // Initialize the thread_status structure for the bootstrap thread
    // This is critical so that k_thread_abort() doesn't crash when accessing
    // _current->callee_saved.thread_status
    bootstrap_thread_status.thread_idx = 0;  // Main thread is always thread 0
    bootstrap_thread_status.aborted = 0;
    bootstrap_thread.callee_saved.thread_status = &bootstrap_thread_status;
    bootstrap_thread.resource_pool = NULL;  // No resource pool for bootstrap thread

    // Set this bootstrap thread as the current thread
    _kernel.cpus[0].current = &bootstrap_thread;

    kernel_initialized = 1;

    MP_ZEPHYR_LOG("Zephyr kernel initialized (Unix/Zephyr threading mode)\n");
}

// Zephyr kernel deinitialization
void mp_zephyr_kernel_deinit(void) {
    // Cleanup if needed
    MP_ZEPHYR_LOG("Zephyr kernel deinitialized (Unix POC mode)\n");
}

// Note: k_uptime_get(), k_uptime_ticks(), k_cycle_get_32(), k_busy_wait()
// are all provided by syscalls/kernel.h stub which forwards to z_impl_* versions

// Additional Zephyr stubs for Unix

// Note: atomic_cas and atomic_set are now defined in kernel_arch_func.h as static inline

// System clock elapsed time (stub)
uint32_t sys_clock_elapsed(void) {
    // Return 0 for Unix POC (not using tickless idle)
    return 0;
}

// Spinlock functions (not needed on Unix single-core)
void arch_spin_relax(void) {
    // No-op on Unix
}

void z_spin_lock_set_owner(struct k_spinlock *l) {
    (void)l;
    // No-op on Unix
}

// System clock timeout (stub - not used in POC)
void sys_clock_set_timeout(k_ticks_t ticks, bool idle) {
    (void)ticks;
    (void)idle;
    // No-op for Unix POC
}

// Note: k_thread_foreach now provided by kernel/thread_monitor.c

// SMP (multi-core) stubs - Unix POC is single-core
struct k_thread *z_smp_current_get(void) {
    // Return NULL for single-core Unix POC
    return NULL;
}

// Note: arch_thread_name_set now provided by arch/posix/core/thread.c

// Object core stubs (statistics/tracing - not needed for POC)
void k_obj_core_init_and_link(struct k_obj_core *obj_core, struct k_obj_type *type) {
    (void)obj_core;
    (void)type;
}

// Note: k_obj_core_stats_register and k_mem_map_phys_guard signatures TBD
// Commented out for now - will add if linker complains

/*
void k_obj_core_stats_register(struct k_obj_core *obj_core, void *stats, size_t stats_len) {
    (void)obj_core;
    (void)stats;
    (void)stats_len;
}

void k_mem_map_phys_guard(uint8_t *addr, size_t size, uint32_t prot, bool clear) {
    (void)addr;
    (void)size;
    (void)prot;
    (void)clear;
}
*/

// Note: z_thread_monitor_lock now provided by kernel/thread_monitor.c

// Provide z_sched_lock() and z_sched_unlock() stubs
// These are normally architecture-specific inline functions
// For Unix POC, we can use a simple mutex
static pthread_mutex_t sched_lock = PTHREAD_MUTEX_INITIALIZER;

void z_sched_lock(void) {
    pthread_mutex_lock(&sched_lock);
}

void z_sched_unlock(void) {
    pthread_mutex_unlock(&sched_lock);
}

// Note: arch_irq_lock/unlock are now provided by arch headers (arch/x86/arch.h)

// Provide arch_is_in_isr() - always false on Unix
bool arch_is_in_isr(void) {
    return false;
}

// Provide z_is_idle_thread_object() stub
bool z_is_idle_thread_object(void *obj) {
    // For now, assume no idle thread in Unix POC
    return false;
}

// Provide k_str_out() for console output
void k_str_out(char *c, size_t n) {
    fwrite(c, 1, n, stdout);
    fflush(stdout);
}

// Provide __printk_hook_install() stub
void __printk_hook_install(int (*fn)(int)) {
    (void)fn;
    // Not needed for Unix POC
}

// Note: z_impl_k_thread_name_set and z_impl_k_thread_name_get now provided by kernel/thread.c

// Provide atomic section functions for MicroPython scheduler
// These are called by mphalport.h MICROPY_BEGIN/END_ATOMIC_SECTION macros
void mp_thread_unix_begin_atomic_section(void) {
    arch_irq_lock();
}

void mp_thread_unix_end_atomic_section(void) {
    arch_irq_unlock(1);  // Always unlock
}

// Note: mp_thread_init(), mp_thread_deinit(), and mp_thread_gc_others()
// are provided by extmod/zephyr_kernel/kernel/mpthread_zephyr.c

// Override POSIX arch function that expects retval member in _callee_saved
// Unix/POSIX doesn't use this, provide stub
void arch_thread_return_value_set(struct k_thread *thread, unsigned int value) {
    (void)thread;
    (void)value;
    // No-op for Unix - not used in POSIX architecture
}

// ============================================================================
// SMP/IPI stubs - Unix POC is single-core, no IPI needed
// ============================================================================

uint32_t ipi_mask_create(struct k_thread *thread) {
    (void)thread;
    return 0;  // No IPI mask needed for single-core
}

void flag_ipi(uint32_t ipi_mask) {
    (void)ipi_mask;
    // No-op for single-core
}

void signal_pending_ipi(void) {
    // No-op for single-core
}

// ============================================================================
// Object core stubs - statistics/tracing not needed for POC
// ============================================================================

int k_obj_core_stats_register(struct k_obj_core *obj_core, void *stats, size_t stats_len) {
    (void)obj_core;
    (void)stats;
    (void)stats_len;
    // Stub - object statistics not needed
    return 0;
}

int k_obj_core_stats_deregister(struct k_obj_core *obj_core) {
    (void)obj_core;
    // Stub - object statistics not needed
    return 0;
}

void k_obj_core_unlink(struct k_obj_core *obj_core) {
    (void)obj_core;
    // Stub - object linking not needed
}

void *k_mem_map_phys_guard(uintptr_t phys, size_t size, uint32_t flags, bool is_anon) {
    (void)phys;
    (void)size;
    (void)flags;
    (void)is_anon;
    // Stub - memory protection not needed for Unix POC
    return NULL;
}

// Note: z_thread_monitor_exit now provided by kernel/thread_monitor.c

// Fatal error handler stub
void z_fatal_error(unsigned int reason, const struct arch_esf *esf) {
    (void)reason;
    (void)esf;
    fprintf(stderr, "Zephyr fatal error: reason=%u\n", reason);
    abort();
}

// Time slice reset stub (time slicing not implemented for POC)
void z_reset_time_slice(struct k_thread *thread) {
    (void)thread;
    // Stub - time slicing not needed for POC
}

// Note: z_thread_entry() is provided by lib/zephyr/lib/os/thread_entry.c
// which calls k_thread_abort() at the end. We no longer override it here
// since our posix_abort_thread() now properly handles thread self-termination.

#endif // MICROPY_ZEPHYR_THREADING
