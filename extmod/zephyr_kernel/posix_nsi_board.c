/*
 * NSI-based POSIX board layer for MicroPython Zephyr integration
 *
 * This provides the board-specific functions that Zephyr's POSIX architecture
 * expects, using NSI (Native Simulator Infrastructure) for thread management.
 *
 * NSI provides proper pthread synchronization via its nct (Native CPU Threading)
 * module, replacing our previous minimal pthread implementation.
 *
 * SPDX-License-Identifier: Apache-2.0
 */

#include <stdio.h>
#include <stdlib.h>
#include <stdarg.h>
#include <stdint.h>
#include <time.h>
#include <sys/time.h>
#include <unistd.h>
#include <pthread.h>
#include <sched.h>

#include <zephyr/kernel.h>
#include <zephyr/arch/posix/posix_soc_if.h>
#include "posix_board_if.h"
#include "posix_core.h"

/* NSI headers from scripts/native_simulator/common/src */
#include "nct_if.h"

// Global state
static struct {
    void *nct_state;            // NSI thread emulator state
    int initialized;
    uint64_t start_time_us;
} board_state = {
    .nct_state = NULL,
    .initialized = 0,
};

// ============================================================================
// NSI stub functions (nct.c requires these for error reporting)
// ============================================================================

// Stub implementations of NSI tracing functions
// NSI's nct.c calls these for error/trace output

void nsi_print_error_and_exit(const char *format, ...) {
    va_list args;
    va_start(args, format);
    fprintf(stderr, "NSI FATAL: ");
    vfprintf(stderr, format, args);
    fprintf(stderr, "\n");
    va_end(args);
    exit(1);
}

void nsi_print_warning(const char *format, ...) {
    va_list args;
    va_start(args, format);
    fprintf(stderr, "NSI WARN: ");
    vfprintf(stderr, format, args);
    fprintf(stderr, "\n");
    va_end(args);
}

void nsi_print_trace(const char *format, ...) {
    va_list args;
    va_start(args, format);
    printf("NSI TRACE: ");
    vprintf(format, args);
    printf("\n");
    va_end(args);
}

void nsi_vprint_error_and_exit(const char *format, va_list vargs) {
    fprintf(stderr, "NSI FATAL: ");
    vfprintf(stderr, format, vargs);
    fprintf(stderr, "\n");
    exit(1);
}

void nsi_vprint_warning(const char *format, va_list vargs) {
    fprintf(stderr, "NSI WARN: ");
    vfprintf(stderr, format, vargs);
    fprintf(stderr, "\n");
}

void nsi_vprint_trace(const char *format, va_list vargs) {
    printf("NSI TRACE: ");
    vprintf(format, vargs);
    printf("\n");
}

int nsi_trace_over_tty(int file_number) {
    return 0;  // Trace to stdout/stderr
}

// ============================================================================
// Board initialization and cleanup (using NSI)
// ============================================================================

void posix_arch_init(void) {
    if (board_state.initialized) {
        return;
    }

    // Record start time for timing functions
    struct timeval tv;
    gettimeofday(&tv, NULL);
    board_state.start_time_us = (uint64_t)tv.tv_sec * 1000000ULL + tv.tv_usec;

    // Initialize NSI thread emulator
    // Pass the entry point that NSI will call when starting each thread
    extern void posix_arch_thread_entry(void *pa_thread_status);
    board_state.nct_state = nct_init(posix_arch_thread_entry);

    board_state.initialized = 1;
}

void posix_arch_clean_up(void) {
    nct_clean_up(board_state.nct_state);
}

// ============================================================================
// Thread management (delegated to NSI's nct module)
// ============================================================================

void posix_swap(int next_allowed_thread_nbr, int this_th_nbr) {
    (void)this_th_nbr;
    nct_swap_threads(board_state.nct_state, next_allowed_thread_nbr);
}

void posix_main_thread_start(int next_allowed_thread_nbr) {
    nct_first_thread_start(board_state.nct_state, next_allowed_thread_nbr);
}

int posix_new_thread(void *payload) {
    return nct_new_thread(board_state.nct_state, payload);
}

void posix_abort_thread(int thread_idx) {
    nct_abort_thread(board_state.nct_state, thread_idx);
}

int posix_arch_get_unique_thread_id(int thread_idx) {
    return nct_get_unique_thread_id(board_state.nct_state, thread_idx);
}

int posix_arch_thread_name_set(int thread_idx, const char *str) {
    return nct_thread_name_set(board_state.nct_state, thread_idx, str);
}

// ============================================================================
// IRQ management stubs (not needed for threading-only integration)
// ============================================================================

void posix_irq_enable(unsigned int irq) {
    // Not needed for minimal threading-only integration
}

void posix_irq_disable(unsigned int irq) {
    // Not needed for minimal threading-only integration
}

int posix_irq_is_enabled(unsigned int irq) {
    return 1;  // Always enabled in minimal mode
}

// Simple mutex for IRQ locking (NSI doesn't provide this)
static pthread_mutex_t irq_lock = PTHREAD_MUTEX_INITIALIZER;

unsigned int posix_irq_lock(void) {
    pthread_mutex_lock(&irq_lock);
    return 0;  // Return dummy key
}

void posix_irq_unlock(unsigned int key) {
    (void)key;
    pthread_mutex_unlock(&irq_lock);
}

void posix_irq_full_unlock(void) {
    // Try to unlock, but don't fail if not locked
    pthread_mutex_unlock(&irq_lock);
}

int posix_get_current_irq(void) {
    return -1;  // No IRQ active
}

void posix_sw_set_pending_IRQ(unsigned int IRQn) {
    // Not needed for minimal threading-only integration
}

void posix_sw_clear_pending_IRQ(unsigned int IRQn) {
    // Not needed for minimal threading-only integration
}

#ifdef CONFIG_IRQ_OFFLOAD
void posix_irq_offload(void (*routine)(const void *), const void *parameter) {
    // Not needed for minimal threading-only integration
    routine(parameter);
}
#endif

// IRQ handler stubs for board_irq.h
void posix_isr_declare(unsigned int irq_p, int flags, void isr_p(const void *),
                       const void *isr_param_p) {
    // Stub - not needed for minimal threading
}

void posix_irq_priority_set(unsigned int irq, unsigned int prio, uint32_t flags) {
    // Stub - not needed for minimal threading
}

#ifdef CONFIG_PM
void posix_irq_check_idle_exit(void) {
    // Stub - PM not supported in minimal mode
}
#endif

// ============================================================================
// CPU halt/idle
// ============================================================================

void posix_halt_cpu(void) {
    // In minimal mode, just yield to let other threads run
    sched_yield();
}

void posix_atomic_halt_cpu(unsigned int imask) {
    (void)imask;
    sched_yield();
}

// ============================================================================
// Timing functions
// ============================================================================

uint64_t posix_get_hw_cycle(void) {
    struct timeval tv;
    gettimeofday(&tv, NULL);
    uint64_t now_us = (uint64_t)tv.tv_sec * 1000000ULL + tv.tv_usec;
    return now_us - board_state.start_time_us;
}

// ============================================================================
// Exit and printing functions (fallbacks, NSI provides its own)
// ============================================================================

void posix_exit(int exit_code) {
    exit(exit_code);
}

// These use the NSI functions we stubbed above
void posix_print_error_and_exit(const char *format, ...) {
    va_list args;
    va_start(args, format);
    nsi_vprint_error_and_exit(format, args);
    va_end(args);
}

void posix_print_warning(const char *format, ...) {
    va_list args;
    va_start(args, format);
    nsi_vprint_warning(format, args);
    va_end(args);
}

void posix_print_trace(const char *format, ...) {
    va_list args;
    va_start(args, format);
    nsi_vprint_trace(format, args);
    va_end(args);
}

void posix_vprint_error_and_exit(const char *format, va_list vargs) {
    nsi_vprint_error_and_exit(format, vargs);
}

void posix_vprint_warning(const char *format, va_list vargs) {
    nsi_vprint_warning(format, vargs);
}

void posix_vprint_trace(const char *format, va_list vargs) {
    nsi_vprint_trace(format, vargs);
}
