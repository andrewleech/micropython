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
#include "py/mphal.h"
#include "py/runtime.h"
#include "py/misc.h"
#include "zephyr_ble_sem.h"
#include "zephyr_ble_work.h"

#include <stddef.h>
#include <stdio.h>

#if ZEPHYR_BLE_DEBUG
#define DEBUG_SEM_printf(...) mp_printf(&mp_plat_print, "SEM: " __VA_ARGS__)
#else
#define DEBUG_SEM_printf(...) do {} while (0)
#endif

// ============================================================================
// FreeRTOS-based implementation (when MICROPY_PY_THREAD is enabled)
// ============================================================================
#if MICROPY_PY_THREAD

#include "FreeRTOS.h"
#include "semphr.h"
#include "task.h"

// Forward declaration of HCI processing function (implemented in port mpzephyrport.c)
extern void mp_bluetooth_zephyr_hci_uart_wfi(void);

// Check if HCI RX task is running (true blocking is safe when task handles HCI)
extern bool mp_bluetooth_zephyr_hci_rx_task_active(void);

void k_sem_init(struct k_sem *sem, unsigned int initial_count, unsigned int limit) {
    DEBUG_SEM_printf("k_sem_init(%p, count=%u, limit=%u)\n", sem, initial_count, limit);

    sem->limit = limit;
    sem->handle = xSemaphoreCreateCountingStatic(limit, initial_count, &sem->storage);

    if (sem->handle == NULL) {
        DEBUG_SEM_printf("  --> FAILED to create semaphore!\n");
    }
}

int k_sem_take(struct k_sem *sem, k_timeout_t timeout) {
    DEBUG_SEM_printf("k_sem_take(%p, timeout=%u)\n", sem, timeout.ticks);

    if (sem->handle == NULL) {
        DEBUG_SEM_printf("  --> semaphore not initialized!\n");
        return -EINVAL;
    }

    // K_NO_WAIT: Non-blocking attempt
    if (timeout.ticks == 0) {
        if (xSemaphoreTake(sem->handle, 0) == pdTRUE) {
            DEBUG_SEM_printf("  --> acquired (no wait)\n");
            return 0;
        }
        DEBUG_SEM_printf("  --> busy\n");
        return -EBUSY;
    }

    // When HCI RX task is running, use true FreeRTOS blocking
    // The HCI RX task processes incoming packets and signals semaphores independently
    if (mp_bluetooth_zephyr_hci_rx_task_active()) {
        TickType_t ticks;
        if (timeout.ticks == 0xFFFFFFFF) {
            ticks = portMAX_DELAY;
        } else {
            ticks = pdMS_TO_TICKS(timeout.ticks);
        }

        DEBUG_SEM_printf("  --> true blocking (HCI RX task active)\n");
        if (xSemaphoreTake(sem->handle, ticks) == pdTRUE) {
            DEBUG_SEM_printf("  --> acquired\n");
            return 0;
        }
        DEBUG_SEM_printf("  --> timeout\n");
        return -EAGAIN;
    }

    // Fallback: poll with short timeouts for HCI processing
    // This is used before HCI RX task is started (during bt_enable setup)
    uint32_t start_ms = mp_hal_ticks_ms();
    uint32_t timeout_ms = (timeout.ticks == 0xFFFFFFFF) ? UINT32_MAX : timeout.ticks;

    DEBUG_SEM_printf("  --> polling mode (HCI RX task not active)\n");
    int poll_count = 0;
    while (1) {
        // Try to take with short timeout (10ms)
        if (xSemaphoreTake(sem->handle, pdMS_TO_TICKS(10)) == pdTRUE) {
            DEBUG_SEM_printf("  --> acquired after %d polls\n", poll_count);
            return 0;
        }

        poll_count++;
        if (poll_count % 10 == 0) {
            mp_printf(&mp_plat_print, "SEM_POLL: count=%d\n", poll_count);
        }

        // Process HCI data that might signal this semaphore
        mp_printf(&mp_plat_print, "SEM_POLL: calling hci_uart_wfi\n");
        mp_bluetooth_zephyr_hci_uart_wfi();

        // Process work items that might signal this semaphore
        // This is critical during init - bt_enable() submits work that must execute
        extern void mp_bluetooth_zephyr_work_process(void);
        mp_printf(&mp_plat_print, "SEM_POLL: calling work_process\n");
        mp_bluetooth_zephyr_work_process();

        // Check for timeout
        uint32_t elapsed = mp_hal_ticks_ms() - start_ms;
        if (timeout_ms != UINT32_MAX && elapsed >= timeout_ms) {
            DEBUG_SEM_printf("  --> timeout after %u ms\n", elapsed);
            return -EAGAIN;
        }
    }
}

void k_sem_give(struct k_sem *sem) {
    DEBUG_SEM_printf("k_sem_give(%p)\n", sem);

    if (sem->handle == NULL) {
        DEBUG_SEM_printf("  --> semaphore not initialized!\n");
        return;
    }

    if (k_sem_in_isr()) {
        // Give from ISR context
        BaseType_t xHigherPriorityTaskWoken = pdFALSE;
        xSemaphoreGiveFromISR(sem->handle, &xHigherPriorityTaskWoken);
        portYIELD_FROM_ISR(xHigherPriorityTaskWoken);
        DEBUG_SEM_printf("  --> gave from ISR\n");
    } else {
        // Give from task context
        xSemaphoreGive(sem->handle);
        DEBUG_SEM_printf("  --> gave from task\n");
    }
}

unsigned int k_sem_count_get(struct k_sem *sem) {
    if (sem->handle == NULL) {
        return 0;
    }
    return (unsigned int)uxSemaphoreGetCount(sem->handle);
}

void k_sem_reset(struct k_sem *sem) {
    DEBUG_SEM_printf("k_sem_reset(%p)\n", sem);

    if (sem->handle == NULL) {
        return;
    }

    // Drain all available counts with iteration limit
    // Limit prevents infinite loop if another task continuously gives
    unsigned int max_iterations = sem->limit + 1;
    unsigned int iterations = 0;
    while (xSemaphoreTake(sem->handle, 0) == pdTRUE && iterations < max_iterations) {
        iterations++;
    }
}

// ============================================================================
// Polling-based implementation (fallback when no RTOS)
// ============================================================================
#else // !MICROPY_PY_THREAD

// Forward declaration of HCI UART processing function
extern void mp_bluetooth_zephyr_hci_uart_wfi(void) __attribute__((weak));

// Weak default implementation
void mp_bluetooth_zephyr_hci_uart_wfi(void) {
    // No-op if not implemented yet
}

void k_sem_init(struct k_sem *sem, unsigned int initial_count, unsigned int limit) {
    DEBUG_SEM_printf("k_sem_init(%p, count=%u, limit=%u)\n", sem, initial_count, limit);
    sem->count = initial_count;
    sem->limit = limit;
}

int k_sem_take(struct k_sem *sem, k_timeout_t timeout) {
    DEBUG_SEM_printf("k_sem_take(%p, timeout=%u)\n", sem, timeout.ticks);

    // Fast path: semaphore available
    if (sem->count > 0) {
        sem->count--;
        DEBUG_SEM_printf("  --> fast path, count now %u\n", sem->count);
        return 0;
    }

    // K_NO_WAIT: Don't block
    if (timeout.ticks == 0) {
        DEBUG_SEM_printf("  --> no wait, returning EBUSY\n");
        return -EBUSY;
    }

    // K_FOREVER: Block indefinitely
    // Regular timeout: Block for specified time
    uint32_t t0 = mp_hal_ticks_ms();
    uint32_t timeout_ms = (timeout.ticks == 0xFFFFFFFF) ? 0xFFFFFFFF : timeout.ticks;

    DEBUG_SEM_printf("  --> waiting (timeout=%u ms)\n", timeout_ms);

    // Set wait loop flag to allow work processing
    extern volatile bool mp_bluetooth_zephyr_in_wait_loop;
    mp_bluetooth_zephyr_in_wait_loop = true;

    while (sem->count == 0) {
        // Process HCI packets
        mp_bluetooth_zephyr_hci_uart_wfi();

        // Check timeout
        uint32_t elapsed = mp_hal_ticks_ms() - t0;
        if (timeout_ms != 0xFFFFFFFF && elapsed >= timeout_ms) {
            DEBUG_SEM_printf("  --> timeout after %u ms\n", elapsed);
            mp_bluetooth_zephyr_in_wait_loop = false;
            return -EAGAIN;
        }

        // Yield
        if (timeout_ms == 0xFFFFFFFF) {
            mp_event_wait_indefinite();
        } else {
            mp_event_wait_ms(0);
        }
    }

    mp_bluetooth_zephyr_in_wait_loop = false;

    // Semaphore became available
    sem->count--;
    DEBUG_SEM_printf("  --> acquired after %u ms, count now %u\n",
        mp_hal_ticks_ms() - t0, sem->count);
    return 0;
}

void k_sem_give(struct k_sem *sem) {
    DEBUG_SEM_printf("k_sem_give(%p)\n", sem);

    if (sem->count < sem->limit) {
        sem->count++;
        DEBUG_SEM_printf("  --> count now %u\n", sem->count);
    } else {
        DEBUG_SEM_printf("  --> at limit, not incrementing\n");
    }
}

unsigned int k_sem_count_get(struct k_sem *sem) {
    return sem->count;
}

void k_sem_reset(struct k_sem *sem) {
    DEBUG_SEM_printf("k_sem_reset(%p)\n", sem);
    sem->count = 0;
}

#endif // MICROPY_PY_THREAD
