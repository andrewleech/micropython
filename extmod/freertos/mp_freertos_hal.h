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
#ifndef MICROPY_INCLUDED_EXTMOD_FREERTOS_MP_FREERTOS_HAL_H
#define MICROPY_INCLUDED_EXTMOD_FREERTOS_MP_FREERTOS_HAL_H

#include "py/mpconfig.h"

#if MICROPY_PY_THREAD

#include "py/obj.h"

// Initialize the FreeRTOS HAL. Must be called from the main Python task.
void mp_freertos_hal_init(void);

// Signal a scheduler event. Called from MICROPY_SCHED_HOOK_SCHEDULED.
void mp_freertos_signal_sched_event(void);

// FreeRTOS-aware delay (wakes on scheduler events)
void mp_freertos_delay_ms(mp_uint_t ms);

// Tick functions (see .c file for overflow warnings)
mp_uint_t mp_freertos_ticks_ms(void);
mp_uint_t mp_freertos_ticks_us(void);

// Yield to scheduler
void mp_freertos_yield(void);

#endif // MICROPY_PY_THREAD

#endif // MICROPY_INCLUDED_EXTMOD_FREERTOS_MP_FREERTOS_HAL_H
