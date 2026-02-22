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

// Kernel stubs required by the Zephyr BLE controller (on-core mode).
// These provide cooperative-mode implementations of Zephyr threading
// and polling APIs used by the controller's HCI driver.

#include <zephyr/kernel.h>
#include <errno.h>

// --- k_poll ---
// In cooperative mode, check semaphore and FIFO state directly.
// The controller's recv_thread uses k_poll to wait on a semaphore
// (new RX data) and a FIFO (host buffers). We check them without blocking.

int k_poll(struct k_poll_event *events, int num_events, k_timeout_t timeout) {
    (void)timeout;

    for (int i = 0; i < num_events; i++) {
        events[i].state = K_POLL_STATE_NOT_READY;

        switch (events[i].type) {
            case K_POLL_TYPE_SEM_AVAILABLE: {
                struct k_sem *sem = (struct k_sem *)events[i].obj;
                if (sem && k_sem_count_get(sem) > 0) {
                    events[i].state = K_POLL_STATE_SEM_AVAILABLE;
                }
                break;
            }
            case K_POLL_TYPE_FIFO_DATA_AVAILABLE: {
                struct k_fifo *fifo = (struct k_fifo *)events[i].obj;
                if (fifo && !k_fifo_is_empty(fifo)) {
                    events[i].state = K_POLL_STATE_FIFO_DATA_AVAILABLE;
                }
                break;
            }
            case K_POLL_TYPE_SIGNAL: {
                struct k_poll_signal *sig = (struct k_poll_signal *)events[i].obj;
                if (sig && sig->signaled) {
                    events[i].state = K_POLL_STATE_SIGNALED;
                    sig->signaled = 0;  // Auto-reset
                }
                break;
            }
            default:
                break;
        }
    }

    // Check if any event is ready
    for (int i = 0; i < num_events; i++) {
        if (events[i].state != K_POLL_STATE_NOT_READY) {
            return 0;
        }
    }

    // Nothing ready — in cooperative mode we return -EAGAIN
    // The caller (recv_thread) will be re-invoked on next poll cycle
    return -11;  // -EAGAIN
}

// --- k_thread_create ---
// In cooperative mode, we don't create real threads. Instead, register
// the thread entry function to be called cooperatively from the main
// polling loop. The controller's recv_thread and prio_recv_thread
// are driven by k_sem_give() waking the cooperative scheduler.

void *k_thread_create(struct k_thread *new_thread,
                      k_thread_stack_t *stack, size_t stack_size,
                      k_thread_entry_t entry,
                      void *p1, void *p2, void *p3,
                      int prio, uint32_t options,
                      k_timeout_t delay) {
    (void)stack;
    (void)stack_size;
    (void)prio;
    (void)options;
    (void)delay;

    if (new_thread) {
        new_thread->entry = entry;
        new_thread->p1 = p1;
        new_thread->p2 = p2;
        new_thread->p3 = p3;
        new_thread->started = true;
    }

    return new_thread;
}

// --- nrf_sys_event ---
// Constant-latency mode control. On nRF52, this enables the POWER
// peripheral's constant latency mode to ensure fast wakeup and consistent
// HFCLK availability for radio timing.
#if defined(NRF52840_XXAA) || defined(NRF52832_XXAA) || defined(NRF52833_XXAA)
#include "nrf.h"
int nrf_sys_event_request_global_constlat(void) {
    NRF_POWER->TASKS_CONSTLAT = 1;
    return 0;
}

int nrf_sys_event_release_global_constlat(void) {
    NRF_POWER->TASKS_LOWPWR = 1;
    return 0;
}
#else
int nrf_sys_event_request_global_constlat(void) {
    return 0;
}

int nrf_sys_event_release_global_constlat(void) {
    return 0;
}
#endif

// find_lsb_set / find_msb_set are provided as static inline by
// zephyr/arch/common/ffs.h (included via sys/util.h chain).

// --- lll_prof stubs ---
// Profiling functions declared in lll_prof_internal.h without #ifdef guards.
// These are no-ops when CONFIG_BT_CTLR_PROFILE_ISR is not defined.
// (lll_prof_enter/exit_* are already static inline no-ops in the header.)
void lll_prof_latency_capture(void) {}
uint16_t lll_prof_latency_get(void) { return 0; }
void lll_prof_radio_end_backup(void) {}
void lll_prof_cputime_capture(void) {}
void lll_prof_send(void) {}

struct node_rx_pdu;  // Forward declaration
struct node_rx_pdu *lll_prof_reserve(void) { return (void *)0; }
void lll_prof_reserve_send(struct node_rx_pdu *rx) { (void)rx; }

// --- HCI UART readchar stub ---
// mp_bluetooth_hci_uart_readchar is used by zephyr_ble_h4.c for UART HCI
// transport. The on-core controller doesn't use UART transport (HCI is
// internal) so this is dead code, but LTO doesn't always eliminate it.
__attribute__((weak))
int mp_bluetooth_hci_uart_readchar(void) { return -1; }

// --- CIS (Connected Isochronous Streams) stubs ---
// The CIS peripheral code in ull_llcp_cc.c is compiled when CONFIG_BT_PERIPHERAL
// is defined but references ISO functions from ull_peripheral_iso.c and
// ull_conn_iso.c which are not compiled (no ISO support). LTO normally
// eliminates these dead code paths but the ARM GCC LTO is unreliable.
// Provide weak stubs to satisfy the linker.
struct pdu_data_llctrl_cis_ind;
struct pdu_data;
struct proc_ctx;
struct ll_conn;

__attribute__((weak))
void ull_peripheral_iso_release(uint16_t cis_handle) { (void)cis_handle; }

__attribute__((weak))
uint8_t ull_peripheral_iso_setup(struct pdu_data_llctrl_cis_ind *ind,
                                 uint8_t cig_id, uint16_t cis_handle,
                                 uint16_t *conn_event_count) {
    (void)ind; (void)cig_id; (void)cis_handle; (void)conn_event_count;
    return 1;  // Return error — CIS not supported
}

__attribute__((weak))
void ull_conn_iso_start(struct ll_conn *conn, uint16_t cis_handle,
                        uint16_t instant, uint16_t conn_event_count,
                        uint8_t is_central) {
    (void)conn; (void)cis_handle; (void)instant;
    (void)conn_event_count; (void)is_central;
}

__attribute__((weak))
void llcp_pdu_decode_cis_ind(struct proc_ctx *ctx, struct pdu_data *pdu) {
    (void)ctx; (void)pdu;
}
