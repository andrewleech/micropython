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
#ifndef MICROPY_INCLUDED_EXTMOD_FREERTOS_MP_FREERTOS_SERVICE_H
#define MICROPY_INCLUDED_EXTMOD_FREERTOS_MP_FREERTOS_SERVICE_H

#include "py/mpconfig.h"

#if MICROPY_PY_THREAD && MICROPY_FREERTOS_SERVICE_TASKS

#include <stdbool.h>
#include <stddef.h>

// ============================================================================
// FreeRTOS Service Task Framework
//
// Provides a high-priority service task for deferred callback execution,
// replacing PendSV interrupt usage which conflicts with FreeRTOS.
//
// This framework:
// - Runs callbacks at highest task priority (similar to lowest interrupt)
// - Supports ISR and task context scheduling
// - Provides suspend/resume for critical sections
// - Uses static allocation for deterministic memory usage
// ============================================================================

// Service dispatch callback type
typedef void (*mp_freertos_dispatch_t)(void);

// ============================================================================
// Configuration macros (ports should define these in mpconfigport.h)
// ============================================================================

// Required: Maximum number of dispatch slots
#ifndef MICROPY_FREERTOS_SERVICE_MAX_SLOTS
#define MICROPY_FREERTOS_SERVICE_MAX_SLOTS (4)
#endif

// Optional: Service task stack size (bytes)
#ifndef MICROPY_FREERTOS_SERVICE_STACK_SIZE
#define MICROPY_FREERTOS_SERVICE_STACK_SIZE (1024)
#endif

// Optional: Service task priority (default: highest)
#ifndef MICROPY_FREERTOS_SERVICE_PRIORITY
#define MICROPY_FREERTOS_SERVICE_PRIORITY (configMAX_PRIORITIES - 1)
#endif

// ============================================================================
// Port-provided function (MUST be defined by each port)
// ============================================================================

// Check if currently executing in ISR context.
// Each port MUST provide this function - no weak default.
// Cortex-M example: check IPSR register (ipsr != 0 means in exception)
bool mp_freertos_service_in_isr(void);

// ============================================================================
// Service Task API
// ============================================================================

// Initialize the service task framework.
// Must be called once after FreeRTOS scheduler is running.
// Safe to call multiple times (idempotent).
void mp_freertos_service_init(void);

// Schedule a callback to run in service task context.
// Safe to call from ISR or task context.
// The callback will run at highest task priority as soon as possible.
// If the same slot is scheduled multiple times before dispatch, only the
// last callback is executed (no queueing per slot).
void mp_freertos_service_schedule(size_t slot, mp_freertos_dispatch_t callback);

// Suspend dispatch processing.
// Blocks the service task from running callbacks.
// Can be called recursively (nesting is tracked).
// Use for critical sections that must not be interrupted by dispatches.
void mp_freertos_service_suspend(void);

// Resume dispatch processing.
// Decrements suspend nesting count and re-notifies if work is pending.
// Must be called once for each call to mp_freertos_service_suspend().
void mp_freertos_service_resume(void);

// Check if a slot has pending work.
// Returns true if the slot has a callback scheduled but not yet executed.
bool mp_freertos_service_is_pending(size_t slot);

#endif // MICROPY_PY_THREAD && MICROPY_FREERTOS_SERVICE_TASKS

#endif // MICROPY_INCLUDED_EXTMOD_FREERTOS_MP_FREERTOS_SERVICE_H
