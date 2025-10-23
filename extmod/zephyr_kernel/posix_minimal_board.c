/*
 * Minimal POSIX board layer for MicroPython Zephyr integration
 *
 * This provides the board-specific functions that Zephyr's POSIX architecture
 * expects, using pthreads for thread management and simple mutexes for IRQ locking.
 *
 * SPDX-License-Identifier: Apache-2.0
 */

#include <pthread.h>
#include <stdio.h>
#include <stdlib.h>
#include <stdarg.h>
#include <stdint.h>
#include <time.h>
#include <sys/time.h>
#include <string.h>

#include <zephyr/kernel.h>
#include <zephyr/arch/posix/posix_soc_if.h>
#include "posix_board_if.h"
#include "posix_core.h"

// Maximum number of threads supported
#define MAX_THREADS 32

// Thread state structure
typedef struct {
    pthread_t pthread;
    void (*entry_point)(void *);
    void *payload;
    int active;
    int aborted;
    char name[16];
} thread_state_t;

// Global state
static struct {
    thread_state_t threads[MAX_THREADS];
    int current_thread_idx;
    int initialized;
    pthread_mutex_t irq_lock;
    pthread_key_t thread_idx_key;
    uint64_t start_time_us;
} board_state = {
    .current_thread_idx = 0,
    .initialized = 0,
    .irq_lock = PTHREAD_MUTEX_INITIALIZER,
};

// Forward declarations
static void *posix_thread_wrapper(void *arg);

// ============================================================================
// Board initialization and cleanup
// ============================================================================

void posix_arch_init(void) {
    if (board_state.initialized) {
        return;
    }

    // Initialize thread local storage key
    pthread_key_create(&board_state.thread_idx_key, NULL);

    // Record start time for timing functions
    struct timeval tv;
    gettimeofday(&tv, NULL);
    board_state.start_time_us = (uint64_t)tv.tv_sec * 1000000ULL + tv.tv_usec;

    // Initialize main thread (thread 0)
    board_state.threads[0].pthread = pthread_self();
    board_state.threads[0].active = 1;
    board_state.threads[0].aborted = 0;
    board_state.current_thread_idx = 0;
    pthread_setspecific(board_state.thread_idx_key, (void *)(intptr_t)0);

    board_state.initialized = 1;
}

void posix_arch_clean_up(void) {
    // Cancel and join all active threads
    for (int i = 1; i < MAX_THREADS; i++) {
        if (board_state.threads[i].active && !board_state.threads[i].aborted) {
            pthread_cancel(board_state.threads[i].pthread);
            pthread_join(board_state.threads[i].pthread, NULL);
        }
    }
    pthread_key_delete(board_state.thread_idx_key);
}

// ============================================================================
// Thread management
// ============================================================================

// Thread wrapper that calls the entry point
static void *posix_thread_wrapper(void *arg) {
    int thread_idx = (int)(intptr_t)arg;
    thread_state_t *ts = &board_state.threads[thread_idx];

    fprintf(stderr, "POSIX: Thread %d starting (pthread=%p, payload=%p)\n",
            thread_idx, (void*)pthread_self(), ts->payload);

    pthread_setspecific(board_state.thread_idx_key, arg);

    // Call the Zephyr thread entry point
    // Note: This should never return - z_thread_entry() calls k_thread_abort()
    // which calls posix_abort_thread() which calls pthread_exit()
    extern void posix_arch_thread_entry(void *pa_thread_status);
    posix_arch_thread_entry(ts->payload);

    // Should never reach here - but if we do, clean exit
    fprintf(stderr, "WARNING: posix_thread_wrapper returned unexpectedly\n");
    ts->active = 0;
    pthread_exit(NULL);
}

// Create a new thread
int posix_new_thread(void *payload) {
    // Find free thread slot
    int thread_idx = -1;
    for (int i = 1; i < MAX_THREADS; i++) {
        if (!board_state.threads[i].active) {
            thread_idx = i;
            break;
        }
    }

    if (thread_idx == -1) {
        fprintf(stderr, "POSIX board: No free thread slots\n");
        return -1;
    }

    thread_state_t *ts = &board_state.threads[thread_idx];
    ts->payload = payload;
    ts->active = 1;
    ts->aborted = 0;

    // Create pthread
    pthread_attr_t attr;
    pthread_attr_init(&attr);
    pthread_attr_setdetachstate(&attr, PTHREAD_CREATE_JOINABLE);

    int ret = pthread_create(&ts->pthread, &attr, posix_thread_wrapper,
                             (void *)(intptr_t)thread_idx);
    pthread_attr_destroy(&attr);

    if (ret != 0) {
        fprintf(stderr, "POSIX board: pthread_create failed: %d\n", ret);
        ts->active = 0;
        return -1;
    }

    return thread_idx;
}

// Abort a thread
void posix_abort_thread(int thread_idx) {
    fprintf(stderr, "POSIX: posix_abort_thread called for thread %d\n", thread_idx);

    if (thread_idx < 0 || thread_idx >= MAX_THREADS) {
        fprintf(stderr, "POSIX: Invalid thread_idx %d\n", thread_idx);
        return;
    }

    thread_state_t *ts = &board_state.threads[thread_idx];
    if (!ts->active || ts->aborted) {
        fprintf(stderr, "POSIX: Thread %d not active or already aborted\n", thread_idx);
        return;
    }

    ts->aborted = 1;

    // If the thread is aborting itself, we cannot pthread_cancel or pthread_join
    // on ourselves - just mark as done and exit via pthread_exit()
    if (pthread_equal(pthread_self(), ts->pthread)) {
        fprintf(stderr, "POSIX: Thread %d aborting itself, calling pthread_exit\n", thread_idx);
        ts->active = 0;
        pthread_exit(NULL);  // This never returns
    }

    // Aborting a different thread - cancel and join
    fprintf(stderr, "POSIX: Thread %d being aborted by another thread, canceling and joining\n", thread_idx);
    pthread_cancel(ts->pthread);
    pthread_join(ts->pthread, NULL);
    ts->active = 0;
    fprintf(stderr, "POSIX: Thread %d abort complete\n", thread_idx);
}

// Get unique thread ID for debugging
int posix_arch_get_unique_thread_id(int thread_idx) {
    return thread_idx;
}

// Set thread name
int posix_arch_thread_name_set(int thread_idx, const char *str) {
    if (thread_idx < 0 || thread_idx >= MAX_THREADS || !str) {
        return -1;
    }

    thread_state_t *ts = &board_state.threads[thread_idx];
    strncpy(ts->name, str, sizeof(ts->name) - 1);
    ts->name[sizeof(ts->name) - 1] = '\0';

#ifdef __linux__
    pthread_setname_np(ts->pthread, ts->name);
#endif

    return 0;
}

// ============================================================================
// Thread context switching
// ============================================================================

void posix_swap(int next_allowed_thread_nbr, int this_th_nbr) {
    // In this minimal implementation, we rely on native pthread scheduling
    // Zephyr's scheduler has already chosen the next thread
    board_state.current_thread_idx = next_allowed_thread_nbr;
    sched_yield();
}

void posix_main_thread_start(int next_allowed_thread_nbr) {
    // Start the main Zephyr thread
    board_state.current_thread_idx = next_allowed_thread_nbr;
}

// ============================================================================
// IRQ management
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

unsigned int posix_irq_lock(void) {
    pthread_mutex_lock(&board_state.irq_lock);
    return 0;  // Return dummy key
}

void posix_irq_unlock(unsigned int key) {
    (void)key;
    pthread_mutex_unlock(&board_state.irq_lock);
}

void posix_irq_full_unlock(void) {
    // Try to unlock, but don't fail if not locked
    pthread_mutex_unlock(&board_state.irq_lock);
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
// Exit and printing functions
// ============================================================================

void posix_exit(int exit_code) {
    exit(exit_code);
}

void posix_print_error_and_exit(const char *format, ...) {
    va_list args;
    va_start(args, format);
    fprintf(stderr, "POSIX FATAL: ");
    vfprintf(stderr, format, args);
    fprintf(stderr, "\n");
    va_end(args);
    exit(1);
}

void posix_print_warning(const char *format, ...) {
    va_list args;
    va_start(args, format);
    fprintf(stderr, "POSIX WARN: ");
    vfprintf(stderr, format, args);
    fprintf(stderr, "\n");
    va_end(args);
}

void posix_print_trace(const char *format, ...) {
    va_list args;
    va_start(args, format);
    printf("POSIX TRACE: ");
    vprintf(format, args);
    printf("\n");
    va_end(args);
}

void posix_vprint_error_and_exit(const char *format, va_list vargs) {
    fprintf(stderr, "POSIX FATAL: ");
    vfprintf(stderr, format, vargs);
    fprintf(stderr, "\n");
    exit(1);
}

void posix_vprint_warning(const char *format, va_list vargs) {
    fprintf(stderr, "POSIX WARN: ");
    vfprintf(stderr, format, vargs);
    fprintf(stderr, "\n");
}

void posix_vprint_trace(const char *format, va_list vargs) {
    printf("POSIX TRACE: ");
    vprintf(format, vargs);
    printf("\n");
}

int posix_trace_over_tty(int file_number) {
    return 0;  // Trace to stdout/stderr
}
