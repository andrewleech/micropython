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

#include "py/mpconfig.h"
#include "zephyr_ble_mutex.h"

#include <assert.h>
#include "py/runtime.h"

#if ZEPHYR_BLE_DEBUG
#define DEBUG_MUTEX_printf(...) mp_printf(&mp_plat_print, "MUTEX: " __VA_ARGS__)
#else
#define DEBUG_MUTEX_printf(...) do {} while (0)
#endif

// ============================================================================
// FreeRTOS-based implementation (when MICROPY_PY_THREAD is enabled)
// ============================================================================
#if MICROPY_PY_THREAD

#include "FreeRTOS.h"
#include "semphr.h"
#include "task.h"
#include <errno.h>

void k_mutex_init(struct k_mutex *mutex) {
    DEBUG_MUTEX_printf("k_mutex_init(%p)\n", mutex);

    // Create recursive mutex with priority inheritance
    mutex->handle = xSemaphoreCreateRecursiveMutexStatic(&mutex->storage);

    if (mutex->handle == NULL) {
        DEBUG_MUTEX_printf("  --> FAILED to create mutex!\n");
    }
}

int k_mutex_lock(struct k_mutex *mutex, k_timeout_t timeout) {
    DEBUG_MUTEX_printf("k_mutex_lock(%p, timeout=%u)\n", mutex, timeout.ticks);

    if (mutex->handle == NULL) {
        DEBUG_MUTEX_printf("  --> mutex not initialized!\n");
        return -EINVAL;
    }

    // Convert timeout to FreeRTOS ticks
    TickType_t ticks;
    if (timeout.ticks == 0) {
        // K_NO_WAIT
        ticks = 0;
    } else if (timeout.ticks == 0xFFFFFFFF) {
        // K_FOREVER
        ticks = portMAX_DELAY;
    } else {
        // Convert milliseconds to ticks
        ticks = pdMS_TO_TICKS(timeout.ticks);
        if (ticks == 0 && timeout.ticks > 0) {
            ticks = 1;
        }
    }

    BaseType_t result = xSemaphoreTakeRecursive(mutex->handle, ticks);

    if (result == pdTRUE) {
        DEBUG_MUTEX_printf("  --> locked\n");
        return 0;
    } else {
        DEBUG_MUTEX_printf("  --> timeout/busy\n");
        return (timeout.ticks == 0) ? -EBUSY : -EAGAIN;
    }
}

int k_mutex_unlock(struct k_mutex *mutex) {
    DEBUG_MUTEX_printf("k_mutex_unlock(%p)\n", mutex);

    if (mutex->handle == NULL) {
        DEBUG_MUTEX_printf("  --> mutex not initialized!\n");
        return -EINVAL;
    }

    BaseType_t result = xSemaphoreGiveRecursive(mutex->handle);

    if (result == pdTRUE) {
        DEBUG_MUTEX_printf("  --> unlocked\n");
        return 0;
    }
    DEBUG_MUTEX_printf("  --> unlock failed (not held?)\n");
    return -EPERM;
}

// ============================================================================
// No-op implementation (fallback when no RTOS)
// ============================================================================
#else // !MICROPY_PY_THREAD

void k_mutex_init(struct k_mutex *mutex) {
    DEBUG_MUTEX_printf("k_mutex_init(%p)\n", mutex);
    mutex->locked = 0;
}

int k_mutex_lock(struct k_mutex *mutex, k_timeout_t timeout) {
    // No-op: All Zephyr BLE code runs in MicroPython scheduler context
    // This provides implicit mutual exclusion (no interruption by other threads)
    // Similar to NimBLE's ble_npl_mutex_pend() implementation
    (void)timeout;

    DEBUG_MUTEX_printf("k_mutex_lock(%p) -> no-op, locked count now %u\n",
        mutex, mutex->locked + 1);

    mutex->locked++;
    return 0;
}

int k_mutex_unlock(struct k_mutex *mutex) {
    DEBUG_MUTEX_printf("k_mutex_unlock(%p) -> no-op, locked count now %u\n",
        mutex, mutex->locked - 1);

    // Assert that mutex was actually locked
    #ifndef NDEBUG
    assert(mutex->locked > 0 && "Unlocking non-locked mutex");
    #else
    if (mutex->locked == 0) {
        // In release builds, silently ignore
        DEBUG_MUTEX_printf("  WARNING: unlocking non-locked mutex\n");
        return 0;
    }
    #endif

    mutex->locked--;
    return 0;
}

#endif // MICROPY_PY_THREAD
