/*
 * This file is part of the MicroPython project, http://micropython.org/
 *
 * The MIT License (MIT)
 *
 * Copyright (c) 2025 MicroPython Developers
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

#ifndef MICROPY_INCLUDED_STM32_MPTHREADPORT_ZEPHYR_H
#define MICROPY_INCLUDED_STM32_MPTHREADPORT_ZEPHYR_H

#if MICROPY_ZEPHYR_THREADING

#include <zephyr/kernel.h>

// Mutex type using Zephyr k_sem (binary semaphore)
// k_sem allows cross-thread lock/unlock (Python Lock semantics)
// k_mutex is recursive which breaks Python Lock semantics
typedef struct _mp_thread_mutex_t {
    struct k_sem handle;
} mp_thread_mutex_t;

// Recursive mutex type (only used when GIL is disabled)
// When MICROPY_PY_THREAD_GIL=1, MICROPY_PY_THREAD_RECURSIVE_MUTEX=0 and this is not used.
#if MICROPY_PY_THREAD_RECURSIVE_MUTEX
typedef struct _mp_thread_recursive_mutex_t {
    struct k_mutex handle;
} mp_thread_recursive_mutex_t;
#endif

// Threading functions (implemented in extmod/zephyr_kernel/mpthread_zephyr.c)
struct _mp_state_thread_t *mp_thread_get_state(void);
void mp_thread_set_state(struct _mp_state_thread_t *state);
bool mp_thread_init_early(void);  // Phase 1: Set thread-local state (before gc_init)
bool mp_thread_init(void *stack);  // Phase 2: Allocate main thread on heap (after gc_init)
void mp_thread_deinit(void);
void mp_thread_gc_others(void);

#endif // MICROPY_ZEPHYR_THREADING

#endif // MICROPY_INCLUDED_STM32_MPTHREADPORT_ZEPHYR_H
