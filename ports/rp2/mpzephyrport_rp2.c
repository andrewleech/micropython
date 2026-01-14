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

// RP2 port integration for Zephyr BLE stack with CYW43 controller

// Suppress deprecation warnings for bt_buf_get_type() - the function still works
// and we need it for H:4 HCI transport
#pragma GCC diagnostic push
#pragma GCC diagnostic ignored "-Wdeprecated-declarations"

// Include board configuration first to get all defines
#include "py/mpconfig.h"

#include "py/runtime.h"
#include "py/stream.h"
#include "py/mphal.h"
#include "extmod/modbluetooth.h"
#include "shared/runtime/softtimer.h"
#include "modmachine.h"

#include <string.h>

#if MICROPY_PY_BLUETOOTH && MICROPY_BLUETOOTH_ZEPHYR

// Include our HAL first (uses our macro definitions)
#include "extmod/zephyr_ble/hal/zephyr_ble_hal.h"

// Undef conflicting macros before including real Zephyr headers
// Our macros are functionally equivalent to Zephyr's but defined without guards
// Pico-SDK KHZ/MHZ vs Zephyr function-like macros
#undef KHZ
#undef MHZ
// Our stubs vs Zephyr util.h
#undef CONTAINER_OF
#undef FLEXIBLE_ARRAY_DECLARE
#include "zephyr/device.h"
#include <zephyr/net_buf.h>
#include <zephyr/bluetooth/buf.h>
#include <zephyr/drivers/bluetooth.h>

// CYW43 driver for WiFi/BT chip
#if MICROPY_PY_NETWORK_CYW43
#include "lib/cyw43-driver/src/cyw43.h"

// Debug: Access to shared bus buffer indices
typedef struct {
    uint32_t host2bt_in_val;
    uint32_t host2bt_out_val;
    uint32_t bt2host_in_val;
    uint32_t bt2host_out_val;
} cybt_fw_membuf_index_t;
extern int cybt_get_bt_buf_index(cybt_fw_membuf_index_t *p_buf_index);
#endif

// Debug output controlled by ZEPHYR_BLE_DEBUG
// Note: debug_printf is NOT thread-safe. Only call from main task context.
// For HCI RX task on core1, use debug_printf_hci_task which is disabled.
// TEMPORARILY enabled for debugging
#define ZEPHYR_BLE_DEBUG_TEMP 0
#if ZEPHYR_BLE_DEBUG || ZEPHYR_BLE_DEBUG_TEMP
#define debug_printf(...) mp_printf(&mp_plat_print, "mpzephyrport_rp2: " __VA_ARGS__)
#else
#define debug_printf(...) do {} while (0)
#endif
// HCI RX task debug is always disabled to prevent multicore printf races
#define debug_printf_hci_task(...) do {} while (0)
#define error_printf(...) mp_printf(&mp_plat_print, "mpzephyrport_rp2 ERROR: " __VA_ARGS__)

// CYW43 SPI btbus HCI transport
#if MICROPY_PY_NETWORK_CYW43

static volatile bt_hci_recv_t recv_cb = NULL;  // Returns int: 0 on success, negative on error
static const struct device *hci_dev = NULL;

// Soft timer for scheduling HCI poll (zero-initialized to prevent startup crashes)
static soft_timer_entry_t mp_zephyr_hci_soft_timer = {0};
static mp_sched_node_t mp_zephyr_hci_sched_node = {0};

// Buffer for incoming HCI packets (4-byte CYW43 header + max HCI packet)
#define CYW43_HCI_HEADER_SIZE 4
#define HCI_MAX_PACKET_SIZE 1024
// IMPORTANT: Must be 4-byte aligned for CYW43 SPI DMA transfers
static uint8_t __attribute__((aligned(4))) hci_rx_buffer[CYW43_HCI_HEADER_SIZE + HCI_MAX_PACKET_SIZE];

// ============================================================================
// FreeRTOS HCI RX Task (Phase 6)
// ============================================================================
#if MICROPY_PY_THREAD
#include "FreeRTOS.h"
#include "task.h"

#define HCI_RX_TASK_STACK_SIZE 1024  // 4KB (in words)
// Lower priority than main thread - HCI RX can wait for main to process
#define HCI_RX_TASK_PRIORITY (tskIDLE_PRIORITY + 1)

static TaskHandle_t hci_rx_task_handle = NULL;
static volatile bool hci_rx_task_running = false;      // Signal task to stop
static volatile bool hci_rx_task_started = false;      // Task has started and is ready
static volatile bool hci_rx_task_exited = false;       // Task has exited
static volatile bool hci_rx_task_shutdown_requested = false;  // Shutdown in progress
static StaticTask_t hci_rx_task_tcb;
static StackType_t hci_rx_task_stack[HCI_RX_TASK_STACK_SIZE];

// Separate buffer for HCI RX task to avoid race with polling code
// IMPORTANT: Must be 4-byte aligned for CYW43 SPI DMA transfers
static uint8_t __attribute__((aligned(4))) hci_rx_task_buffer[CYW43_HCI_HEADER_SIZE + HCI_MAX_PACKET_SIZE];

// Simple HCI packet queue for thread-safe handoff from HCI RX task to main task
// Using a ring buffer with fixed-size slots
#define HCI_RX_QUEUE_SIZE 16
#define HCI_RX_SLOT_SIZE (CYW43_HCI_HEADER_SIZE + 256)  // Most HCI packets are small
static uint8_t hci_rx_queue[HCI_RX_QUEUE_SIZE][HCI_RX_SLOT_SIZE] __attribute__((aligned(4)));
static uint16_t hci_rx_queue_len[HCI_RX_QUEUE_SIZE];  // Length of each packet
static volatile uint8_t hci_rx_queue_head = 0;  // Write index (HCI RX task)
static volatile uint8_t hci_rx_queue_tail = 0;  // Read index (main task)
static volatile uint32_t hci_rx_queue_dropped = 0;  // Packets dropped due to full queue

// Debug counters for HCI RX task (volatile for cross-thread access)
static volatile uint32_t hci_rx_task_polls = 0;
static volatile uint32_t hci_rx_task_packets = 0;

// HCI event type counters (for debugging what packets are received)
static volatile uint32_t hci_rx_evt_cmd_complete = 0;  // 0x0E
static volatile uint32_t hci_rx_evt_cmd_status = 0;    // 0x0F
static volatile uint32_t hci_rx_evt_le_meta = 0;       // 0x3E (LE events including ADV_REPORT)
static volatile uint32_t hci_rx_evt_le_adv_report = 0; // 0x3E subevent 0x02 (advertising reports)
static volatile uint32_t hci_rx_evt_other = 0;         // Other event codes
static volatile uint32_t hci_rx_acl = 0;               // ACL data packets

// Rejection counters (for debugging validation) - non-static for k_panic debug output
volatile uint32_t hci_rx_rejected_len = 0;       // Invalid length
volatile uint32_t hci_rx_rejected_param_len = 0; // param_len mismatch
volatile uint32_t hci_rx_rejected_oversize = 0;  // Oversized packet
volatile uint32_t hci_rx_rejected_event = 0;     // Unknown event code
volatile uint32_t hci_rx_rejected_acl = 0;       // Invalid ACL
volatile uint32_t hci_rx_rejected_type = 0;      // Unknown packet type
volatile uint32_t hci_rx_buf_failed = 0;         // Buffer alloc failed
volatile uint32_t hci_rx_total_processed = 0;    // Total packets processed

// Process a single HCI packet from the given buffer
static void process_hci_rx_packet(uint8_t *rx_buf, uint32_t len) {
    if (recv_cb == NULL || len <= CYW43_HCI_HEADER_SIZE) {
        return;
    }

    hci_rx_total_processed++;

    // Extract packet type from CYW43 header (byte 3)
    uint8_t pkt_type = rx_buf[3];
    uint8_t *pkt_data = &rx_buf[CYW43_HCI_HEADER_SIZE];
    uint32_t pkt_len = len - CYW43_HCI_HEADER_SIZE;

    // Debug: log CMD_COMPLETE and CMD_STATUS events (important for init)
    // NOTE: Disabled - excessive logging during init causes timing issues
    #if 0
    if (pkt_type == BT_HCI_H4_EVT && pkt_len >= 2) {
        uint8_t evt = pkt_data[0];
        if (evt == 0x0E || evt == 0x0F) {  // CMD_COMPLETE or CMD_STATUS
            mp_printf(&mp_plat_print, "[HCI_RX] evt=0x%02x len=%lu\n", evt, (unsigned long)pkt_len);
        }
    }
    #endif

    // Validate packet length - HCI event packets should be reasonable size
    // Event header is: event_code(1) + param_len(1) + params(param_len)
    // Maximum reasonable size is ~255 bytes (param_len is uint8)
    if (pkt_len > 260 || pkt_len < 2) {
        // Invalid packet length - likely garbage data, skip it
        hci_rx_rejected_len++;
        return;
    }

    // Allocate Zephyr net_buf based on packet type
    struct net_buf *buf = NULL;

    switch (pkt_type) {
        case BT_HCI_H4_EVT:
            if (pkt_len >= 2) {
                // Track event types for debugging
                uint8_t evt_code = pkt_data[0];
                uint8_t param_len = pkt_data[1];

                // Validate: param_len should match actual packet length
                // pkt_len = event_code(1) + param_len(1) + params(param_len)
                if (param_len + 2 != pkt_len) {
                    // Length mismatch - corrupted packet, skip
                    hci_rx_rejected_param_len++;
                    return;
                }

                // Validate event code - must be a known HCI event
                // Check event code BEFORE size check because different events use
                // different buffer pools with different size limits:
                // - CMD_COMPLETE (0x0E), CMD_STATUS (0x0F): hci_cmd_pool (larger, ~255 bytes)
                // - Other events: hci_rx_pool (CONFIG_BT_BUF_EVT_RX_SIZE = 68)
                bool valid_event = false;
                bool is_cmd_event = false;
                if (evt_code == 0x0E) {
                    hci_rx_evt_cmd_complete++;
                    valid_event = true;
                    is_cmd_event = true;
                } else if (evt_code == 0x0F) {
                    hci_rx_evt_cmd_status++;
                    valid_event = true;
                    is_cmd_event = true;
                } else if (evt_code == 0x3E) {
                    hci_rx_evt_le_meta++;
                    valid_event = true;
                    // Check LE subevent code (at pkt_data[2] after evt_code and length)
                    if (pkt_len >= 3 && pkt_data[2] == 0x02) {
                        hci_rx_evt_le_adv_report++;
                    }
                } else if (evt_code == 0x05 || evt_code == 0x08 ||
                           evt_code == 0x13 || evt_code == 0x1A ||
                           evt_code == 0x04 || evt_code == 0x03) {
                    // Other valid events
                    hci_rx_evt_other++;
                    valid_event = true;
                }
                // Skip unknown event codes - likely garbage
                if (!valid_event) {
                    hci_rx_rejected_event++;
                    return;
                }

                // Check packet fits in buffer pool
                // CMD_COMPLETE/CMD_STATUS use hci_cmd_pool (255 bytes)
                // Other events use hci_rx_pool (CONFIG_BT_BUF_EVT_RX_SIZE = 68)
                uint32_t max_evt_size = is_cmd_event ? 255 : 68;
                if (pkt_len > max_evt_size) {
                    hci_rx_rejected_oversize++;
                    return;
                }

                buf = bt_buf_get_evt(pkt_data[0], false, K_FOREVER);
            }
            break;
        case BT_HCI_H4_ACL:
            // ACL data: handle(2) + length(2) + data
            if (pkt_len < 4) {
                hci_rx_rejected_acl++;
                return;  // Too short
            }
            // Check declared length matches actual
            uint16_t acl_len = pkt_data[2] | (pkt_data[3] << 8);
            if (acl_len + 4 != pkt_len) {
                hci_rx_rejected_acl++;
                return;  // Length mismatch
            }
            // Check fits in buffer (CONFIG_BT_BUF_ACL_RX_SIZE = 27)
            if (pkt_len > 27 + 4) {  // 27 data + 4 header
                hci_rx_rejected_acl++;
                return;  // Oversized
            }
            hci_rx_acl++;
            buf = bt_buf_get_rx(BT_BUF_ACL_IN, K_FOREVER);
            break;
        default:
            // Unknown packet type - silently ignore (catches garbage H4 types)
            hci_rx_rejected_type++;
            return;
    }

    if (buf == NULL) {
        // Don't use mp_printf here - HCI RX task doesn't hold GIL
        // Just silently drop the packet
        hci_rx_buf_failed++;
        return;
    }

    // Copy packet data to net_buf and deliver to Zephyr
    net_buf_add_mem(buf, pkt_data, pkt_len);

    // Set work queue context flag so priority HCI events (like Number of Completed Packets)
    // can directly process TX notify instead of queuing work. This is needed because
    // priority events are processed immediately in bt_recv_unsafe(), not via work queue.
    extern void mp_bluetooth_zephyr_set_sys_work_q_context(bool in_context);
    mp_bluetooth_zephyr_set_sys_work_q_context(true);

    int ret = recv_cb(hci_dev, buf);

    mp_bluetooth_zephyr_set_sys_work_q_context(false);

    if (ret < 0) {
        // Error - unref the buffer (no mp_printf from HCI RX task)
        net_buf_unref(buf);
    }
}

// Queue a packet from HCI RX task for processing by main task
// Returns true if queued, false if queue full
static bool hci_rx_queue_packet(uint8_t *data, uint32_t len) {
    uint8_t next_head = (hci_rx_queue_head + 1) % HCI_RX_QUEUE_SIZE;

    // Check if queue is full
    if (next_head == hci_rx_queue_tail) {
        hci_rx_queue_dropped++;
        return false;
    }

    // Truncate if packet too large for slot
    if (len > HCI_RX_SLOT_SIZE) {
        len = HCI_RX_SLOT_SIZE;
    }

    // Copy packet to queue
    memcpy(hci_rx_queue[hci_rx_queue_head], data, len);
    hci_rx_queue_len[hci_rx_queue_head] = len;

    // Memory barrier to ensure data is written before index update
    __asm volatile ("dmb" ::: "memory");

    hci_rx_queue_head = next_head;
    return true;
}

// Check if Zephyr BT buffer pools have free buffers available
// Returns true if at least one buffer can be allocated without blocking
static bool mp_bluetooth_zephyr_buffers_available(void) {
    // Try to allocate a buffer with K_NO_WAIT to test availability
    // If successful, immediately free it and return true
    struct net_buf *buf = bt_buf_get_rx(BT_BUF_EVT, K_NO_WAIT);
    if (buf) {
        net_buf_unref(buf);
        return true;
    }
    return false;
}

// Process all queued HCI packets - called from main task context
void mp_bluetooth_zephyr_process_hci_queue(void) {
    while (hci_rx_queue_tail != hci_rx_queue_head) {
        // Check buffer availability before processing
        // If no buffers free, process work to release some
        if (!mp_bluetooth_zephyr_buffers_available()) {
            // Process pending Zephyr work items which may free buffers
            extern void mp_bluetooth_zephyr_work_process(void);
            mp_bluetooth_zephyr_work_process();

            // Check again - if still no buffers, stop processing and let
            // the next poll cycle handle the remaining queue
            if (!mp_bluetooth_zephyr_buffers_available()) {
                // This is unusual - buffer exhaustion shouldn't happen
                // in normal operation. Return and retry on next poll.
                return;
            }
        }

        uint8_t *pkt = hci_rx_queue[hci_rx_queue_tail];
        uint32_t len = hci_rx_queue_len[hci_rx_queue_tail];

        // Process this packet in main task context where it's safe
        process_hci_rx_packet(pkt, len);

        // Move to next slot
        hci_rx_queue_tail = (hci_rx_queue_tail + 1) % HCI_RX_QUEUE_SIZE;
    }
}

// HCI RX task - runs continuously, polls CYW43 for incoming HCI data
// NOTE: This runs on core0, don't use debug_printf (printf race condition)
static void hci_rx_task_func(void *arg) {
    (void)arg;
    debug_printf_hci_task("HCI RX task started\n");

    extern int cyw43_bluetooth_hci_read(uint8_t *buf, uint32_t max_size, uint32_t *len);

    // Signal that we've started and are ready
    hci_rx_task_started = true;

    while (hci_rx_task_running) {
        // Check for shutdown notification (non-blocking)
        // This allows immediate wakeup instead of waiting for vTaskDelay timeout
        uint32_t notification = ulTaskNotifyTake(pdTRUE, 0);
        if (notification && hci_rx_task_shutdown_requested) {
            debug_printf_hci_task("HCI RX task shutdown requested\n");
            break;
        }

        // Take local copy of recv_cb for consistency (avoid TOCTOU race)
        bt_hci_recv_t cb = recv_cb;
        if (cb != NULL) {
            // Poll for HCI data
            uint32_t len = 0;
            hci_rx_task_polls++;  // Track poll count

            // cyw43_bluetooth_hci_read() internally acquires CYW43_THREAD_ENTER via cyw43_ensure_bt_up()
            int ret = cyw43_bluetooth_hci_read(hci_rx_task_buffer, sizeof(hci_rx_task_buffer), &len);

            if (ret == 0 && len > CYW43_HCI_HEADER_SIZE) {
                hci_rx_task_packets++;  // Track received packets
                // Queue for main task processing instead of calling recv_cb directly
                hci_rx_queue_packet(hci_rx_task_buffer, len);
            }
        }

        // Yield to other tasks - 10ms poll interval
        vTaskDelay(pdMS_TO_TICKS(10));
    }

    debug_printf_hci_task("HCI RX task exiting\n");

    // Signal exit before deleting (avoids undefined eTaskGetState behavior)
    hci_rx_task_exited = true;
    vTaskDelete(NULL);
}

// Start HCI RX task - called during BLE initialization
void mp_bluetooth_zephyr_hci_rx_task_start(void) {
    if (hci_rx_task_handle != NULL) {
        debug_printf("HCI RX task already running\n");
        return;
    }

    debug_printf("Starting HCI RX task\n");
    hci_rx_task_running = true;
    hci_rx_task_started = false;
    hci_rx_task_exited = false;

    // Reset debug counters and queue state
    hci_rx_task_polls = 0;
    hci_rx_task_packets = 0;
    hci_rx_evt_cmd_complete = 0;
    hci_rx_evt_cmd_status = 0;
    hci_rx_evt_le_meta = 0;
    hci_rx_evt_le_adv_report = 0;
    hci_rx_evt_other = 0;
    hci_rx_acl = 0;
    hci_rx_queue_head = 0;
    hci_rx_queue_tail = 0;
    hci_rx_queue_dropped = 0;

    // On SMP builds, pin HCI RX task to core0 to avoid potential
    // SPI/CYW43 driver issues with cross-core access.
    // Core0 is where CYW43 is initialized and GPIO IRQs are handled.
    #if configNUMBER_OF_CORES > 1
    hci_rx_task_handle = xTaskCreateStaticAffinitySet(
        hci_rx_task_func,
        "hci_rx",
        HCI_RX_TASK_STACK_SIZE,
        NULL,
        HCI_RX_TASK_PRIORITY,
        hci_rx_task_stack,
        &hci_rx_task_tcb,
        (1 << 0)  // Pin to core0
        );
    #else
    hci_rx_task_handle = xTaskCreateStatic(
        hci_rx_task_func,
        "hci_rx",
        HCI_RX_TASK_STACK_SIZE,
        NULL,
        HCI_RX_TASK_PRIORITY,
        hci_rx_task_stack,
        &hci_rx_task_tcb
        );
    #endif

    if (hci_rx_task_handle == NULL) {
        error_printf("Failed to create HCI RX task\n");
        hci_rx_task_running = false;
    }
}

// Stop HCI RX task - called during BLE deinitialization
// Uses task notification for immediate wakeup to avoid 10ms delay
void mp_bluetooth_zephyr_hci_rx_task_stop(void) {
    if (hci_rx_task_handle == NULL) {
        return;
    }

    debug_printf("Stopping HCI RX task: polls=%lu packets=%lu started=%d dropped=%lu\n",
        (unsigned long)hci_rx_task_polls, (unsigned long)hci_rx_task_packets,
        (int)hci_rx_task_started, (unsigned long)hci_rx_queue_dropped);
    debug_printf("  HCI events: cmd_complete=%lu cmd_status=%lu le_meta=%lu (adv=%lu) other=%lu acl=%lu\n",
        (unsigned long)hci_rx_evt_cmd_complete, (unsigned long)hci_rx_evt_cmd_status,
        (unsigned long)hci_rx_evt_le_meta, (unsigned long)hci_rx_evt_le_adv_report,
        (unsigned long)hci_rx_evt_other, (unsigned long)hci_rx_acl);

    // Phase 1: Signal shutdown intent (but keep recv_cb set for polling fallback)
    // After task stops, bt_disable() and other HCI operations will use polling mode
    // which needs recv_cb to be valid. recv_cb will be cleared in port_deinit().
    hci_rx_task_shutdown_requested = true;

    // Phase 2: Signal task to stop and ensure visibility
    hci_rx_task_running = false;
    __atomic_thread_fence(__ATOMIC_SEQ_CST);

    // Phase 3: Notify task to wake immediately (don't wait for vTaskDelay timeout)
    xTaskNotifyGive(hci_rx_task_handle);

    // Phase 4: Wait for clean exit with shorter timeout (task wakes immediately)
    TickType_t start = xTaskGetTickCount();
    const TickType_t max_wait = pdMS_TO_TICKS(200);  // 200ms timeout (was 1s)

    while (!hci_rx_task_exited) {
        if ((xTaskGetTickCount() - start) > max_wait) {
            error_printf("HCI RX task exit timeout!\n");
            break;
        }
        vTaskDelay(pdMS_TO_TICKS(5));
    }

    // Phase 5: Reset state for next init cycle
    hci_rx_task_handle = NULL;
    hci_rx_task_shutdown_requested = false;

    // Drain any stale packets from queue (they won't be processed anyway)
    hci_rx_queue_head = hci_rx_queue_tail;

    debug_printf("HCI RX task stopped\n");
}

// Check if HCI RX task is active and ready
bool mp_bluetooth_zephyr_hci_rx_task_active(void) {
    // Only return true once task has fully started and is processing HCI data
    return hci_rx_task_handle != NULL && hci_rx_task_running && hci_rx_task_started;
}

// Get HCI RX task debug counters
void mp_bluetooth_zephyr_hci_rx_task_debug(uint32_t *polls, uint32_t *packets) {
    if (polls) {
        *polls = hci_rx_task_polls;
    }
    if (packets) {
        *packets = hci_rx_task_packets;
    }
}

// Get HCI RX queue dropped counter
uint32_t mp_bluetooth_zephyr_hci_rx_queue_dropped(void) {
    return hci_rx_queue_dropped;
}

#else // !MICROPY_PY_THREAD

// Stubs for non-FreeRTOS builds
void mp_bluetooth_zephyr_hci_rx_task_start(void) {
}

void mp_bluetooth_zephyr_hci_rx_task_stop(void) {
}

bool mp_bluetooth_zephyr_hci_rx_task_active(void) {
    return false;
}

uint32_t mp_bluetooth_zephyr_hci_rx_queue_dropped(void) {
    return 0;
}

#endif // MICROPY_PY_THREAD
// ============================================================================

// Forward declarations
static void mp_zephyr_hci_poll_now(void);
void mp_bluetooth_zephyr_port_poll_in_ms(uint32_t ms);

// This is called by soft_timer and executes at PendSV level
static void mp_zephyr_hci_soft_timer_callback(soft_timer_entry_t *self) {
    mp_zephyr_hci_poll_now();
}

// HCI packet reception handler - called when data arrives from CYW43 SPI
static void run_zephyr_hci_task(mp_sched_node_t *node) {
    (void)node;

    // Early exit if BLE is not active (recv_cb is set by hci_cyw43_open)
    // This prevents processing stale Zephyr state after soft reset
    if (recv_cb == NULL) {
        // Don't use mp_printf here - it can trigger scheduler recursion
        return;
    }

    // Process Zephyr BLE work queues and semaphores
    mp_bluetooth_zephyr_poll();

    // Process any queued HCI packets from the HCI RX task
    // This must be done in main task context for thread safety with Zephyr
    #if MICROPY_PY_THREAD
    mp_bluetooth_zephyr_process_hci_queue();

    // Skip direct HCI reading if dedicated HCI RX task is active
    // The task handles HCI reception and queuing
    if (mp_bluetooth_zephyr_hci_rx_task_active()) {
        return;
    }
    #endif

    // Fallback: Read directly from CYW43 via shared SPI bus
    // This path is only used when HCI RX task is not active

    // Check buffer availability before reading HCI data
    if (!mp_bluetooth_zephyr_buffers_available()) {
        // No buffers - process work to free some
        extern void mp_bluetooth_zephyr_work_process(void);
        mp_bluetooth_zephyr_work_process();

        // If still no buffers, skip this poll cycle but reschedule
        if (!mp_bluetooth_zephyr_buffers_available()) {
            mp_bluetooth_zephyr_port_poll_in_ms(10);
            return;
        }
    }

    extern int cyw43_bluetooth_hci_read(uint8_t *buf, uint32_t max_size, uint32_t *len);
    uint32_t len = 0;
    int ret = cyw43_bluetooth_hci_read(hci_rx_buffer, sizeof(hci_rx_buffer), &len);

    if (ret == 0 && len > CYW43_HCI_HEADER_SIZE) {
        // Use process_hci_rx_packet for consistent validation
        process_hci_rx_packet(hci_rx_buffer, len);
    }

    // Reschedule soft timer for continuous HCI polling (10ms interval)
    // This is critical for receiving scan results and other HCI events
    mp_bluetooth_zephyr_port_poll_in_ms(10);
}

static void mp_zephyr_hci_poll_now(void) {
    // Note: mp_printf can crash during BLE init - avoid debug output here
    mp_sched_schedule_node(&mp_zephyr_hci_sched_node, run_zephyr_hci_task);
}

// Zephyr HCI driver implementation

// Forward declarations for poll_uart stats (defined later in file)
extern volatile uint32_t poll_uart_count;
extern volatile uint32_t poll_uart_hci_reads;
extern volatile uint32_t poll_uart_cyw43_calls;
extern volatile uint32_t poll_uart_skipped_recursion;
extern volatile uint32_t poll_uart_skipped_no_cb;
extern volatile uint32_t hci_tx_count;
extern volatile uint32_t hci_tx_cmd_count;

static int hci_cyw43_open(const struct device *dev, bt_hci_recv_t recv) {
    debug_printf("hci_cyw43_open called, dev=%p recv=%p\n", dev, recv);
    hci_dev = dev;

    // Reset poll_uart counters for fresh start
    poll_uart_count = 0;
    poll_uart_hci_reads = 0;
    poll_uart_cyw43_calls = 0;
    poll_uart_skipped_recursion = 0;
    poll_uart_skipped_no_cb = 0;
    hci_tx_count = 0;
    hci_tx_cmd_count = 0;

    // Note: recv_cb is set AFTER bt_hci_transport_setup() to prevent HCI RX
    // task from reading SPI bus during BT firmware download

    // Initialize CYW43 BT controller via bt_hci_transport_setup()
    // This must be called before any HCI communication can happen
    int ret = bt_hci_transport_setup(dev);
    if (ret != 0) {
        error_printf("bt_hci_transport_setup failed: %d\n", ret);
        return ret;
    }

    // Flush any stale HCI data from previous session
    // This prevents old responses from confusing the new init sequence
    extern int cyw43_bluetooth_hci_read(uint8_t *buf, uint32_t max_size, uint32_t *len);
    int flush_count = 0;
    uint32_t len = 0;
    while (cyw43_bluetooth_hci_read(hci_rx_buffer, sizeof(hci_rx_buffer), &len) == 0 && len > 0) {
        flush_count++;
        len = 0;
        if (flush_count > 100) {
            break;                     // Safety limit
        }
    }
    if (flush_count > 0) {
        debug_printf("Flushed %d stale HCI packets\n", flush_count);
    }

    // Now enable HCI RX by setting callback (HCI RX task is already running)
    recv_cb = recv;

    debug_printf("hci_cyw43_open completed\n");
    return 0;
}

static int hci_cyw43_close(const struct device *dev) {
    // Print poll_uart stats (always available)
    debug_printf("hci_cyw43_close: poll_uart calls=%lu hci_reads=%lu cyw43_calls=%lu\n",
        (unsigned long)poll_uart_count, (unsigned long)poll_uart_hci_reads,
        (unsigned long)poll_uart_cyw43_calls);

    // Print HCI RX stats before closing
    #if MICROPY_PY_THREAD
    debug_printf("  HCI RX task: polls=%lu packets=%lu\n",
        (unsigned long)hci_rx_task_polls, (unsigned long)hci_rx_task_packets);
    mp_bluetooth_zephyr_hci_rx_task_stop();
    #endif

    recv_cb = NULL;
    soft_timer_remove(&mp_zephyr_hci_soft_timer);

    // Teardown the HCI transport to allow clean reinitialization
    bt_hci_transport_teardown(dev);

    return 0;
}

static int hci_cyw43_send(const struct device *dev, struct net_buf *buf) {
    (void)dev;
    uint8_t buf_type = bt_buf_get_type(buf);
    debug_printf("hci_cyw43_send: type=%u len=%u data[0]=0x%02x\n", buf_type, buf->len, buf->len > 0 ? buf->data[0] : 0xFF);

    // Debug: increment TX counter
    extern volatile uint32_t hci_tx_count;
    extern volatile uint32_t hci_tx_cmd_count;
    hci_tx_count++;

    // Map Zephyr buffer type to H:4 packet type
    uint8_t pkt_type;
    switch (buf_type) {
        case BT_BUF_CMD:
            hci_tx_cmd_count++;
            pkt_type = BT_HCI_H4_CMD;
            break;
        case BT_BUF_ACL_OUT:
            pkt_type = BT_HCI_H4_ACL;
            break;
        default:
            error_printf("Unknown buffer type: %u\n", buf_type);
            net_buf_unref(buf);
            return -1;
    }

    // CYW43 requires 4-byte header: [0,0,0,pkt_type] + packet_data
    // Allocate temporary buffer for CYW43 packet format
    size_t cyw43_pkt_size = CYW43_HCI_HEADER_SIZE + buf->len;
    uint8_t *cyw43_pkt = m_new(uint8_t, cyw43_pkt_size);

    // Build CYW43 packet: 4-byte header + HCI data
    memset(cyw43_pkt, 0, CYW43_HCI_HEADER_SIZE);
    cyw43_pkt[3] = pkt_type;
    memcpy(&cyw43_pkt[CYW43_HCI_HEADER_SIZE], buf->data, buf->len);

    // Write to CYW43 via shared SPI bus
    extern int cyw43_bluetooth_hci_write(uint8_t *buf, size_t len);
    int ret = cyw43_bluetooth_hci_write(cyw43_pkt, cyw43_pkt_size);

    m_del(uint8_t, cyw43_pkt, cyw43_pkt_size);
    net_buf_unref(buf);

    if (ret != 0) {
        error_printf("cyw43_bluetooth_hci_write failed: %d\n", ret);
        return -1;
    }

    return 0;
}

// HCI driver API structure
static const struct bt_hci_driver_api hci_cyw43_api = {
    .open = hci_cyw43_open,
    .close = hci_cyw43_close,
    .send = hci_cyw43_send,
};

// Device state (required for device_is_ready())
static struct device_state hci_device_state = {
    .init_res = 0,
    .initialized = true,
};

// HCI device structure (referenced by Zephyr via DEVICE_DT_GET macro)
// Named __device_dts_ord_0 to match what DEVICE_DT_GET() expands to
// __attribute__((used)) prevents garbage collection with -fdata-sections
__attribute__((used))
const struct device __device_dts_ord_0 = {
    .name = "HCI_CYW43",
    .api = &hci_cyw43_api,
    .state = &hci_device_state,
    .data = NULL,
};

// Alias for code that uses the descriptive name
const struct device *const mp_bluetooth_zephyr_hci_dev = &__device_dts_ord_0;

// CYW43 BT uses shared SPI bus (btbus), no UART HAL needed

// HCI transport setup (called by BLE host during initialization)
int bt_hci_transport_setup(const struct device *dev) {
    (void)dev;

    // Initialize CYW43 BT using shared SPI bus (same as BTstack)
    // This ensures WiFi driver is up first, then loads BT firmware
    extern int cyw43_bluetooth_hci_init(void);
    return cyw43_bluetooth_hci_init();
}

// HCI transport teardown
int bt_hci_transport_teardown(const struct device *dev) {
    debug_printf("bt_hci_transport_teardown\n");
    (void)dev;

    // CYW43 btbus doesn't have a deinit function - BT state is maintained
    // The cyw43_bluetooth_hci_init() is idempotent (checks bt_loaded flag)
    return 0;
}

// Initialize Zephyr port
void mp_bluetooth_zephyr_port_init(void) {
    // Initialize soft timer for HCI polling
    // Always enabled - needed as fallback when HCI RX task is disabled
    soft_timer_static_init(
        &mp_zephyr_hci_soft_timer,
        SOFT_TIMER_MODE_ONE_SHOT,
        0,
        mp_zephyr_hci_soft_timer_callback
        );
}

// Schedule HCI poll
void mp_bluetooth_zephyr_port_poll_in_ms(uint32_t ms) {
    soft_timer_reinsert(&mp_zephyr_hci_soft_timer, ms);
}

// Poll HCI from CYW43 SPI - called from k_sem_take() wait loop
// This reads any pending HCI data from the CYW43 chip and passes it to Zephyr
static volatile bool poll_uart_in_progress = false;

// Debug counter for poll_uart calls (non-static for k_panic debug output)
volatile uint32_t poll_uart_count = 0;
volatile uint32_t poll_uart_hci_reads = 0;  // Successful HCI reads
volatile uint32_t hci_tx_count = 0;  // HCI commands/ACL data sent
volatile uint32_t hci_tx_cmd_count = 0;  // HCI commands only

uint32_t mp_bluetooth_zephyr_poll_uart_count(void) {
    return poll_uart_count;
}

uint32_t mp_bluetooth_zephyr_poll_uart_hci_reads(void) {
    return poll_uart_hci_reads;
}

// Debug: track poll_uart entry reasons
volatile uint32_t poll_uart_skipped_recursion = 0;
volatile uint32_t poll_uart_skipped_no_cb = 0;
volatile uint32_t poll_uart_skipped_task = 0;
volatile uint32_t poll_uart_cyw43_calls = 0;

// Deinitialize Zephyr port - called during ble.active(False)
void mp_bluetooth_zephyr_port_deinit(void) {
    // Remove soft timer to stop HCI polling during shutdown
    soft_timer_remove(&mp_zephyr_hci_soft_timer);

    // Clear recv_cb since bt_disable() has reset the controller
    // On reinit, bt_enable() will set up a fresh HCI transport
    recv_cb = NULL;

    // Clear the scheduler node callback to prevent execution after deinit.
    // The scheduler queue persists across soft reset, and scheduler.c:103-106 has a safety
    // check that skips NULL callbacks. This prevents the callback from trying to access
    // BLE state or CYW43 after deinitialization, which could cause hangs during soft reset.
    // We're in main thread context here, so it's safe to modify the node.
    mp_zephyr_hci_sched_node.callback = NULL;

    // DO NOT reset bt_loaded - the CYW43 BT firmware should stay loaded.
    // bt_disable() sends HCI_Reset which resets the controller state.
    // On reinit, bt_enable() will send another HCI_Reset to the already-loaded firmware.
    // Re-downloading firmware to an already-running controller corrupts its state.

    // Reset the HOST_CTRL register cache in the shared bus driver.
    // This is critical because cybt_reg_read() returns cached values for HOST_CTRL_REG_ADDR.
    // After bt_disable(), the BT controller state may have changed but the cache is stale.
    // This affects wake signaling in cybt_set_bt_awake() used by cybt_bus_request().
    //
    // Must reset to SW_RDY (1 << 24) not 0, because:
    // - btbus_init sets SW_RDY to tell firmware host is ready
    // - cybt_toggle_bt_intr() XORs DATA_VALID based on cached value
    // - If cache is 0, the toggle will clear SW_RDY which breaks firmware comms
    extern volatile uint32_t host_ctrl_cache_reg;
    host_ctrl_cache_reg = (1 << 24);  // BTSDIO_REG_SW_RDY_BITMASK

    // Reset state variables for clean re-initialization
    poll_uart_in_progress = false;
    poll_uart_count = 0;
    poll_uart_hci_reads = 0;
    hci_tx_count = 0;
    hci_tx_cmd_count = 0;
    poll_uart_skipped_recursion = 0;
    poll_uart_skipped_no_cb = 0;
    poll_uart_skipped_task = 0;
    poll_uart_cyw43_calls = 0;

    #if MICROPY_PY_THREAD
    // Reset HCI RX task flags so next init starts fresh
    hci_rx_task_started = false;
    hci_rx_task_exited = false;
    hci_rx_task_shutdown_requested = false;
    #endif
}

void mp_bluetooth_zephyr_poll_uart(void) {
    poll_uart_count++;

    // Prevent recursion
    if (poll_uart_in_progress) {
        poll_uart_skipped_recursion++;
        return;
    }
    if (recv_cb == NULL) {
        poll_uart_skipped_no_cb++;
        return;
    }

    poll_uart_in_progress = true;

    // Process any packets queued by HCI RX task first
    // This is critical for timely command credit return
    #if MICROPY_PY_THREAD
    mp_bluetooth_zephyr_process_hci_queue();

    // If HCI RX task is running, it handles all packet reading from CYW43.
    // We only process the queue here to avoid race condition.
    if (hci_rx_task_running) {
        poll_uart_in_progress = false;
        return;
    }
    #endif

    poll_uart_cyw43_calls++;

    // Read ALL available HCI packets from CYW43 (like BTstack does)
    // Loop until no more data available
    // NOTE: This path is only used when HCI RX task is NOT running
    extern int cyw43_bluetooth_hci_read(uint8_t *buf, uint32_t max_size, uint32_t *len);
    bool has_work;
    do {
        // Check buffer availability before reading more HCI data
        // This prevents reading data we can't process
        if (!mp_bluetooth_zephyr_buffers_available()) {
            // No buffers - process work to free some
            extern void mp_bluetooth_zephyr_work_process(void);
            mp_bluetooth_zephyr_work_process();

            // If still no buffers, stop reading and let next poll handle it
            if (!mp_bluetooth_zephyr_buffers_available()) {
                break;
            }
        }

        uint32_t len = 0;
        int ret = cyw43_bluetooth_hci_read(hci_rx_buffer, sizeof(hci_rx_buffer), &len);

        if (ret == 0 && len > CYW43_HCI_HEADER_SIZE) {
            poll_uart_hci_reads++;  // Track successful HCI reads
            has_work = true;

            // Use process_hci_rx_packet for consistent validation
            process_hci_rx_packet(hci_rx_buffer, len);
        } else {
            has_work = false;
        }
    } while (has_work);

    poll_uart_in_progress = false;
}

#else // !MICROPY_PY_NETWORK_CYW43

// Stub implementations for ports without CYW43
void mp_bluetooth_zephyr_port_init(void) {
}

void mp_bluetooth_zephyr_port_poll_in_ms(uint32_t ms) {
    (void)ms;
}

#endif // MICROPY_PY_NETWORK_CYW43

#endif // MICROPY_PY_BLUETOOTH && MICROPY_BLUETOOTH_ZEPHYR
