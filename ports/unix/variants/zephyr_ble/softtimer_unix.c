/*
 * This file is part of the MicroPython project, http://micropython.org/
 *
 * The MIT License (MIT)
 *
 * Copyright (c) 2025 MicroPython Contributors
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

// Pthread-based soft timer backend for the Unix port.
// Implements the soft_timer_get_ms() / soft_timer_schedule_at_ms() / soft_timer_init()
// interface required by shared/runtime/softtimer.c when MICROPY_SOFT_TIMER_TICKS_MS
// is not defined.
//
// A background pthread sleeps until the next scheduled expiry, then calls
// soft_timer_handler() which runs timer callbacks. The PENDSV enter/exit
// functions use a recursive pthread mutex for thread safety.

#include <pthread.h>
#include <time.h>
#include <stdbool.h>
#include <stdint.h>
#include <errno.h>

#include "py/runtime.h"
#include "shared/runtime/softtimer.h"

// Recursive mutex for PendSV-equivalent critical sections.
// Must be recursive because soft timer C callbacks may call soft_timer_insert()
// which calls PENDSV_ENTER (same mutex).
static pthread_mutex_t pendsv_mutex;
static bool pendsv_mutex_inited = false;

// Timer thread state.
static pthread_t timer_thread_id;
static pthread_mutex_t timer_mutex = PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t timer_cond = PTHREAD_COND_INITIALIZER;
static volatile uint32_t timer_target_ms = 0;
static volatile bool timer_scheduled = false;
static volatile bool timer_thread_running = false;

void mp_unix_pendsv_enter(void) {
    if (pendsv_mutex_inited) {
        pthread_mutex_lock(&pendsv_mutex);
    }
}

void mp_unix_pendsv_exit(void) {
    if (pendsv_mutex_inited) {
        pthread_mutex_unlock(&pendsv_mutex);
    }
}

uint32_t soft_timer_get_ms(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (uint32_t)(ts.tv_sec * 1000 + ts.tv_nsec / 1000000);
}

void soft_timer_schedule_at_ms(uint32_t ticks_ms) {
    pthread_mutex_lock(&timer_mutex);
    timer_target_ms = ticks_ms;
    timer_scheduled = true;
    pthread_cond_signal(&timer_cond);
    pthread_mutex_unlock(&timer_mutex);
}

static void *soft_timer_thread(void *arg) {
    (void)arg;

    while (timer_thread_running) {
        pthread_mutex_lock(&timer_mutex);

        // Wait until a timer is scheduled.
        while (!timer_scheduled && timer_thread_running) {
            pthread_cond_wait(&timer_cond, &timer_mutex);
        }

        if (!timer_thread_running) {
            pthread_mutex_unlock(&timer_mutex);
            break;
        }

        // Compute absolute time for condvar wait.
        uint32_t target = timer_target_ms;
        timer_scheduled = false;
        pthread_mutex_unlock(&timer_mutex);

        // Wait until the target time or until re-scheduled.
        uint32_t now = soft_timer_get_ms();
        int32_t delay = (int32_t)(target - now);

        if (delay > 0) {
            struct timespec ts;
            clock_gettime(CLOCK_REALTIME, &ts);
            ts.tv_sec += delay / 1000;
            ts.tv_nsec += (delay % 1000) * 1000000L;
            if (ts.tv_nsec >= 1000000000L) {
                ts.tv_sec++;
                ts.tv_nsec -= 1000000000L;
            }

            pthread_mutex_lock(&timer_mutex);
            // If re-scheduled while we were computing, loop back.
            if (timer_scheduled) {
                pthread_mutex_unlock(&timer_mutex);
                continue;
            }
            int ret = pthread_cond_timedwait(&timer_cond, &timer_mutex, &ts);
            bool was_rescheduled = timer_scheduled;
            pthread_mutex_unlock(&timer_mutex);

            // If signalled (re-scheduled), loop back to pick up new target.
            if (ret != ETIMEDOUT && was_rescheduled) {
                continue;
            }
        }

        // Timer expired -- call handler under PendSV lock.
        mp_unix_pendsv_enter();
        soft_timer_handler();
        mp_unix_pendsv_exit();
    }

    return NULL;
}

// Called early at process startup via constructor attribute, and also
// explicitly from port_init. The idempotent checks prevent double init.
__attribute__((constructor))
void soft_timer_init(void) {
    // Initialise recursive mutex.
    if (!pendsv_mutex_inited) {
        pthread_mutexattr_t attr;
        pthread_mutexattr_init(&attr);
        pthread_mutexattr_settype(&attr, PTHREAD_MUTEX_RECURSIVE);
        pthread_mutex_init(&pendsv_mutex, &attr);
        pthread_mutexattr_destroy(&attr);
        pendsv_mutex_inited = true;
    }

    // Start the timer thread.
    if (!timer_thread_running) {
        timer_thread_running = true;
        timer_scheduled = false;
        pthread_create(&timer_thread_id, NULL, soft_timer_thread, NULL);
    }
}
