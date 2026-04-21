/*
 * This file is part of the MicroPython project, http://micropython.org/
 *
 * The MIT License (MIT)
 *
 * Copyright (c) 2026 Andrew Leech
 *
 * Permission is hereby granted, free of charge, to any person obtaining a copy
 * of this software and associated documentation files (the "Software"), to deal
 * in the Software without restriction, including without limitation the rights
 * to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
 * copies of the Software, and to permit persons to whom the Software is
 * furnished to do so, subject to the following conditions:
 *
 * The above copyright notice and this permission notice shall be included in
 * all copies or substantial portions of the Software.
 *
 * THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
 * IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
 * FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
 * AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
 * LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
 * OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
 * THE SOFTWARE.
 */

// Soft timer backend for Unix port using pthread + condition variable.
//
// The shared softtimer.c framework calls:
//   soft_timer_get_ms() -- return current ms tick
//   soft_timer_schedule_at_ms(ticks_ms) -- wake timer thread at given time
//
// soft_timer_handler() runs in the timer thread context (PendSV-equivalent).
// MICROPY_PY_PENDSV_ENTER/EXIT protect the timer heap via recursive mutex
// shared between the timer thread and the main thread.

#include <pthread.h>
#include <time.h>
#include <stdbool.h>
#include <errno.h>
#include <signal.h>

#include "py/runtime.h"
#include "shared/runtime/softtimer.h"

static pthread_t main_thread;
static pthread_t timer_thread;
static pthread_mutex_t timer_mutex;  // recursive, shared with PENDSV macros
static pthread_cond_t timer_cond;
static volatile bool timer_running;
volatile uint32_t timer_target_ms;
volatile bool timer_scheduled;

// --- Port API for softtimer.c ---

uint32_t soft_timer_get_ms(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (uint32_t)(ts.tv_sec * 1000 + ts.tv_nsec / 1000000);
}

// Track the timer thread ID so we can skip condvar signal when called
// from within the timer callback (would deadlock).
static pthread_t timer_thread;
static volatile bool in_timer_callback = false;

void soft_timer_schedule_at_ms(uint32_t ticks_ms) {
    // Just set the target atomically.  Never call pthread_cond_signal here
    // because it can deadlock on the condvar's internal futex when the timer
    // threads are in pthread_cond_timedwait.  The timer thread will wake
    // naturally when its current timedwait expires (at most 128ms latency)
    // and re-evaluate timer_scheduled / timer_target_ms.
    timer_target_ms = ticks_ms;
    __atomic_store_n(&timer_scheduled, true, __ATOMIC_RELEASE);
}

// --- Timer Thread ---

static void *timer_thread_func(void *arg) {
    (void)arg;
    pthread_mutex_lock(&timer_mutex);
    while (timer_running) {
        if (!timer_scheduled) {
            // No timer pending -- wait indefinitely for signal.
            // Use timed wait (not indefinite) since soft_timer_schedule_at_ms
            // no longer signals the condvar.  Poll every 50ms.
            struct timespec idle_ts;
            clock_gettime(CLOCK_MONOTONIC, &idle_ts);
            idle_ts.tv_nsec += 50000000L; // 50ms
            if (idle_ts.tv_nsec >= 1000000000L) {
                idle_ts.tv_sec++;
                idle_ts.tv_nsec -= 1000000000L;
            }
            pthread_cond_timedwait(&timer_cond, &timer_mutex, &idle_ts);
            continue;
        }

        // Calculate delay until target time.
        uint32_t now = soft_timer_get_ms();
        int32_t delay = (int32_t)(timer_target_ms - now);

        if (delay > 0) {
            struct timespec ts;
            clock_gettime(CLOCK_MONOTONIC, &ts);
            ts.tv_sec += delay / 1000;
            ts.tv_nsec += (long)(delay % 1000) * 1000000L;
            if (ts.tv_nsec >= 1000000000L) {
                ts.tv_sec++;
                ts.tv_nsec -= 1000000000L;
            }
            int ret = pthread_cond_timedwait(&timer_cond, &timer_mutex, &ts);
            if (ret == 0) {
                // Signaled -- re-evaluate (target may have changed).
                continue;
            }
            // ETIMEDOUT -- fall through to fire handler.
        }

        // Timer expired -- fire handler.
        timer_scheduled = false;
        // soft_timer_handler modifies the heap, protected by this mutex
        // (same mutex as PENDSV_ENTER/EXIT).
        in_timer_callback = true;
        soft_timer_handler();
        in_timer_callback = false;
    }
    pthread_mutex_unlock(&timer_mutex);
    return NULL;
}

// --- Init/Deinit ---

// Empty signal handler -- the signal's only purpose is to interrupt blocking
// syscalls (select, poll, etc.) with EINTR so the main thread re-checks
// pending scheduled callbacks.
static void sigusr1_handler(int sig) {
    (void)sig;
}

void soft_timer_init(void) {
    main_thread = pthread_self();

    // Install SIGUSR1 handler to interrupt blocking syscalls.
    struct sigaction sa;
    sa.sa_handler = sigusr1_handler;
    sa.sa_flags = 0; // No SA_RESTART -- we want EINTR.
    sigemptyset(&sa.sa_mask);
    sigaction(SIGUSR1, &sa, NULL);

    // Initialize recursive mutex (PENDSV_ENTER may be called from callbacks
    // that already hold this mutex via the timer thread).
    pthread_mutexattr_t attr;
    pthread_mutexattr_init(&attr);
    pthread_mutexattr_settype(&attr, PTHREAD_MUTEX_RECURSIVE);
    pthread_mutex_init(&timer_mutex, &attr);
    pthread_mutexattr_destroy(&attr);

    // Use CLOCK_MONOTONIC for the condvar to match soft_timer_get_ms()
    // which also uses CLOCK_MONOTONIC.  Avoids clock skew from system
    // time adjustments causing spurious timeouts or missed wakeups.
    pthread_condattr_t cond_attr;
    pthread_condattr_init(&cond_attr);
    pthread_condattr_setclock(&cond_attr, CLOCK_MONOTONIC);
    pthread_cond_init(&timer_cond, &cond_attr);
    pthread_condattr_destroy(&cond_attr);
    timer_running = true;
    timer_scheduled = false;
    pthread_create(&timer_thread, NULL, timer_thread_func, NULL);
}

// Called during interpreter shutdown to stop the timer thread.
void soft_timer_deinit_port(void) {
    if (!timer_running) {
        return;
    }
    pthread_mutex_lock(&timer_mutex);
    timer_running = false;
    pthread_cond_signal(&timer_cond);
    pthread_mutex_unlock(&timer_mutex);
    pthread_join(timer_thread, NULL);
    pthread_mutex_destroy(&timer_mutex);
    pthread_cond_destroy(&timer_cond);
}

// --- PENDSV mutex (called from MICROPY_PY_PENDSV_ENTER/EXIT macros) ---

void mp_unix_pendsv_enter(void) {
    pthread_mutex_lock(&timer_mutex);
}

void mp_unix_pendsv_exit(void) {
    pthread_mutex_unlock(&timer_mutex);
}

// --- Main thread wake (called from MICROPY_SCHED_HOOK_SCHEDULED) ---

void mp_unix_wake_main_thread(void) {
    // Send SIGUSR1 to the main thread to interrupt any blocking syscall
    // (e.g. select() in time.sleep()) so it processes scheduled callbacks.
    // No-op if called from the main thread itself.
    if (!pthread_equal(pthread_self(), main_thread)) {
        pthread_kill(main_thread, SIGUSR1);
    }
}
