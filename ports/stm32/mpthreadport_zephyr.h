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

// Mutex types using Zephyr k_sem (matches ports/zephyr pattern)
typedef struct _mp_thread_mutex_t {
    struct k_sem handle;
} mp_thread_mutex_t;

typedef struct _mp_thread_recursive_mutex_t {
    struct k_sem handle;
} mp_thread_recursive_mutex_t;

// Threading functions (implemented in extmod/zephyr_kernel/mpthread_zephyr.c)
bool mp_thread_init(void *stack);
void mp_thread_deinit(void);
void mp_thread_gc_others(void);

#endif // MICROPY_ZEPHYR_THREADING

#endif // MICROPY_INCLUDED_STM32_MPTHREADPORT_ZEPHYR_H
