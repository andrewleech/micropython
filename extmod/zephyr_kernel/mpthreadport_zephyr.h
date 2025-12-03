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

// Common Zephyr threading definitions for MicroPython ports.
// This file should be included from port-specific mpthreadport.h files.

#ifndef MICROPY_INCLUDED_EXTMOD_ZEPHYR_KERNEL_MPTHREADPORT_ZEPHYR_H
#define MICROPY_INCLUDED_EXTMOD_ZEPHYR_KERNEL_MPTHREADPORT_ZEPHYR_H

#include <zephyr/kernel.h>

// Mutex type using Zephyr k_sem (binary semaphore).
// k_sem allows cross-thread lock/unlock (Python Lock semantics).
typedef struct _mp_thread_mutex_t {
    struct k_sem handle;
} mp_thread_mutex_t;

// Recursive mutex type (only used when GIL is disabled).
#if MICROPY_PY_THREAD_RECURSIVE_MUTEX
typedef struct _mp_thread_recursive_mutex_t {
    struct k_mutex handle;
} mp_thread_recursive_mutex_t;
#endif

// Threading functions (implemented in extmod/zephyr_kernel/kernel/mpthread_zephyr.c).
struct _mp_state_thread_t *mp_thread_get_state(void);
void mp_thread_set_state(struct _mp_state_thread_t *state);
bool mp_thread_init_early(void);  // Phase 1: Set thread-local state (before gc_init)
bool mp_thread_init(void *stack);  // Phase 2: Allocate main thread on heap (after gc_init)
void mp_thread_deinit(void);
void mp_thread_gc_others(void);

// GIL exit with yield for cooperative scheduling.
// The VM's GIL bounce code does: MP_THREAD_GIL_EXIT(); MP_THREAD_GIL_ENTER();
// Without k_yield() after unlock, the same thread immediately re-acquires
// the GIL before other threads can run (thread_coop.py fails).
void mp_thread_gil_exit(void);

// Override the default MP_THREAD_GIL_EXIT to use our function with k_yield().
#undef MP_THREAD_GIL_EXIT
#define MP_THREAD_GIL_EXIT() mp_thread_gil_exit()

#endif // MICROPY_INCLUDED_EXTMOD_ZEPHYR_KERNEL_MPTHREADPORT_ZEPHYR_H
