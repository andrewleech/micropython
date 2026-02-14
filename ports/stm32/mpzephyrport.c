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

// STM32 port integration for Zephyr BLE stack
// Works with both UART HCI transport and STM32WB IPCC transport

#include "py/mpconfig.h"

// Enable hard fault debug output for diagnostics
#include "stm32_it.h"

#if MICROPY_PY_BLUETOOTH && MICROPY_BLUETOOTH_ZEPHYR

#include "py/runtime.h"
#include "py/mphal.h"
#include "extmod/modbluetooth.h"
#include "extmod/mpbthci.h"
#include "extmod/zephyr_ble/hal/zephyr_ble_poll.h"
#include "extmod/zephyr_ble/hal/zephyr_ble_port.h"
#include "extmod/zephyr_ble/hal/zephyr_ble_h4.h"

// Don't include mpbthciport.h - it defines mp_bluetooth_hci_poll_now as static inline
// which conflicts with our non-inline definition needed by modbluetooth_zephyr.c.
// Instead, declare the specific functions we need from mpbthciport.c:
extern void mp_bluetooth_hci_poll_now_default(void);

#include <string.h>

#include "extmod/zephyr_ble/hal/zephyr_ble_hal.h"
#include "zephyr/device.h"
#include <zephyr/net_buf.h>
#include <zephyr/drivers/bluetooth.h>

// Suppress deprecation warning for bt_buf_get_type()
// The function is deprecated in Zephyr but still works and is the standard API
#pragma GCC diagnostic push
#pragma GCC diagnostic ignored "-Wdeprecated-declarations"
#include <zephyr/bluetooth/buf.h>
#pragma GCC diagnostic pop

// Debug macros - conditional based on ZEPHYR_BLE_DEBUG
#if ZEPHYR_BLE_DEBUG
#define DEBUG_HCI_printf(...) mp_printf(&mp_plat_print, "HCI: " __VA_ARGS__)
#else
#define DEBUG_HCI_printf(...) do {} while (0)
#endif

// Error messages are always printed
#define error_printf(...) mp_printf(&mp_plat_print, "HCI ERROR: " __VA_ARGS__)

// H:4 packet types
#define H4_CMD  0x01
#define H4_ACL  0x02
#define H4_SCO  0x03
#define H4_EVT  0x04

// Zephyr HCI driver callback and device
static const struct device *hci_dev = NULL;
static bt_hci_recv_t recv_cb = NULL;

// Queue for completed HCI packets (received from interrupt context)
// These are deferred for processing in scheduler context to avoid stack overflow
// Increased from 8 to 32 to handle burst of advertising reports during scanning
#define RX_QUEUE_SIZE 32
static struct net_buf *rx_queue[RX_QUEUE_SIZE];
static volatile size_t rx_queue_head = 0;
static volatile size_t rx_queue_tail = 0;


// RX queue helpers (can be called from IRQ context)
static inline bool rx_queue_is_full(void) {
    return ((rx_queue_head + 1) % RX_QUEUE_SIZE) == rx_queue_tail;
}

static inline bool rx_queue_is_empty(void) {
    return rx_queue_head == rx_queue_tail;
}

static bool rx_queue_put(struct net_buf *buf) {
    MICROPY_PY_BLUETOOTH_ENTER
    if (rx_queue_is_full()) {
        MICROPY_PY_BLUETOOTH_EXIT
        return false;
    }
    rx_queue[rx_queue_head] = buf;
    rx_queue_head = (rx_queue_head + 1) % RX_QUEUE_SIZE;
    MICROPY_PY_BLUETOOTH_EXIT
    return true;
}

static struct net_buf *rx_queue_get(void) {
    MICROPY_PY_BLUETOOTH_ENTER
    if (rx_queue_is_empty()) {
        MICROPY_PY_BLUETOOTH_EXIT
        return NULL;
    }
    struct net_buf *buf = rx_queue[rx_queue_tail];
    rx_queue_tail = (rx_queue_tail + 1) % RX_QUEUE_SIZE;
    MICROPY_PY_BLUETOOTH_EXIT
    return buf;
}

// HCI Event Priority Sorting for STM32WB55 IPCC
// The RF coprocessor can send CONNECTION_COMPLETE and DISCONNECT_COMPLETE
// in the same IPCC transaction, causing wrong event ordering.
// We batch-collect events and sort so connection events precede disconnect.

// HCI event codes
#define HCI_EVT_DISCONNECT_COMPLETE 0x05
#define HCI_EVT_CMD_COMPLETE        0x0E
#define HCI_EVT_LE_META             0x3E
#define HCI_LE_SUBEVENT_CONN_COMPLETE           0x01
#define HCI_LE_SUBEVENT_ENHANCED_CONN_COMPLETE  0x0A

// Event priority: lower number = higher priority (processed first)
#define HCI_PRIO_CONNECTION  1  // LE Connection Complete
#define HCI_PRIO_DEFAULT     5  // Most events
#define HCI_PRIO_DISCONNECT  9  // Disconnect Complete (process last)

// Get event priority for sorting (connection events before disconnect)
static int hci_event_get_priority(struct net_buf *buf) {
    if (buf == NULL || buf->len < 4) {
        return HCI_PRIO_DEFAULT;
    }

    const uint8_t *data = buf->data;
    // H4 packet: [type][evt_code][len][params...]
    if (data[0] != H4_EVT) {
        return HCI_PRIO_DEFAULT;  // Not an event
    }

    uint8_t evt_code = data[1];

    // LE Meta Event - check subevent
    if (evt_code == HCI_EVT_LE_META && buf->len >= 4) {
        uint8_t subevent = data[3];
        if (subevent == HCI_LE_SUBEVENT_CONN_COMPLETE ||
            subevent == HCI_LE_SUBEVENT_ENHANCED_CONN_COMPLETE) {
            return HCI_PRIO_CONNECTION;  // High priority
        }
    }

    // Disconnect Complete
    if (evt_code == HCI_EVT_DISCONNECT_COMPLETE) {
        return HCI_PRIO_DISCONNECT;  // Low priority
    }

    return HCI_PRIO_DEFAULT;
}

// Get connection handle from HCI event (for grouping related events)
static uint16_t hci_event_get_conn_handle(struct net_buf *buf) {
    if (buf == NULL || buf->len < 6) {
        return 0xFFFF;  // Invalid handle
    }

    const uint8_t *data = buf->data;
    if (data[0] != H4_EVT) {
        return 0xFFFF;
    }

    uint8_t evt_code = data[1];

    // LE Connection Complete: [type=04][evt=3E][len][subevent=01][status][handle_lo][handle_hi]...
    if (evt_code == HCI_EVT_LE_META && buf->len >= 7) {
        uint8_t subevent = data[3];
        if (subevent == HCI_LE_SUBEVENT_CONN_COMPLETE ||
            subevent == HCI_LE_SUBEVENT_ENHANCED_CONN_COMPLETE) {
            // Handle is at offset 5-6 (after subevent and status)
            return (data[5] | (data[6] << 8)) & 0x0FFF;
        }
    }

    // Disconnect Complete: [type=04][evt=05][len=4][status][handle_lo][handle_hi][reason]
    if (evt_code == HCI_EVT_DISCONNECT_COMPLETE && buf->len >= 6) {
        // Handle is at offset 4-5 (after status)
        return (data[4] | (data[5] << 8)) & 0x0FFF;
    }

    return 0xFFFF;
}

// Sort batch of HCI events by priority (simple insertion sort, batch is small)
// Events with same connection handle are grouped, with connection events first
static void hci_event_sort_batch(struct net_buf **batch, int count) {
    if (count <= 1) {
        return;
    }

    // Simple insertion sort - batch is typically 2-4 events
    for (int i = 1; i < count; i++) {
        struct net_buf *key = batch[i];
        int key_prio = hci_event_get_priority(key);
        uint16_t key_handle = hci_event_get_conn_handle(key);

        int j = i - 1;
        while (j >= 0) {
            int j_prio = hci_event_get_priority(batch[j]);
            uint16_t j_handle = hci_event_get_conn_handle(batch[j]);

            // Sort by: (1) connection handle, (2) priority within same handle
            bool should_swap = false;
            if (key_handle == j_handle && key_handle != 0xFFFF) {
                // Same connection: sort by priority
                should_swap = (key_prio < j_prio);
            } else if (key_prio < j_prio) {
                // Different connections: connection events first overall
                should_swap = (key_prio == HCI_PRIO_CONNECTION && j_prio == HCI_PRIO_DISCONNECT);
            }

            if (should_swap) {
                batch[j + 1] = batch[j];
                j--;
            } else {
                break;
            }
        }
        batch[j + 1] = key;
    }
}

// Callback for mp_bluetooth_hci_uart_readpacket() - called for each byte.
// Uses shared H:4 parser from extmod/zephyr_ble/hal/zephyr_ble_h4.c.
// IMPORTANT: This may be called from interrupt context (IPCC IRQ on STM32WB)
// DO NOT call recv_cb() directly - queue the buffer for processing in scheduler context
static void h4_uart_byte_callback(uint8_t byte) {
    struct net_buf *buf = mp_bluetooth_zephyr_h4_process_byte(byte);
    if (buf) {
        #if ZEPHYR_BLE_DEBUG
        // Debug: Trace HCI packets (type is first byte in buffer)
        uint8_t pkt_type = buf->data[0];
        if (pkt_type == H4_ACL) {
            uint16_t handle = (buf->data[1] | (buf->data[2] << 8)) & 0x0FFF;
            uint16_t acl_len = buf->data[3] | (buf->data[4] << 8);
            DEBUG_HCI_printf("RX ACL: handle=0x%03x len=%d, first_byte=0x%02x\n",
                handle, acl_len, (buf->len > 9) ? buf->data[9] : 0);
        } else if (pkt_type == H4_EVT) {
            uint8_t evt_code = buf->data[1];
            if (evt_code == HCI_EVT_DISCONNECT_COMPLETE) {
                uint8_t status = buf->data[3];
                uint16_t handle = (buf->data[4] | (buf->data[5] << 8)) & 0x0FFF;
                uint8_t reason = buf->data[6];
                DEBUG_HCI_printf("RX DISCONNECT: handle=0x%03x status=%d reason=0x%02x\n",
                    handle, status, reason);
            } else if (evt_code == HCI_EVT_CMD_COMPLETE) {
                uint16_t opcode = buf->data[4] | (buf->data[5] << 8);
                DEBUG_HCI_printf("RX CMD_COMPLETE: opcode=0x%04x\n", opcode);
            }
        }
        #endif

        // Queue the buffer for processing in scheduler context
        // This avoids calling bt_hci_recv() from interrupt context
        if (!rx_queue_put(buf)) {
            error_printf("RX queue full\n");
            net_buf_unref(buf);
        } else {
            // Schedule task to process queued packets
            mp_bluetooth_zephyr_port_poll_now();
        }
    }
}

// HCI packet reception handler - called from shared sched_node via soft timer.
// Strong override of weak default in zephyr_ble_poll.c.
void mp_bluetooth_zephyr_port_run_task(mp_sched_node_t *node) {
    (void)node;

    // Process Zephyr BLE work queues and semaphores
    mp_bluetooth_zephyr_poll();

    if (recv_cb == NULL) {
        return;
    }

    // Process any queued RX buffers (from interrupt context)
    // STM32WB55 IPCC Fix: Batch collect and sort events to ensure CONNECTION_COMPLETE
    // is processed before DISCONNECT_COMPLETE when both arrive in same transaction.
    #define HCI_EVENT_BATCH_SIZE 16
    struct net_buf *batch[HCI_EVENT_BATCH_SIZE];
    int batch_count = 0;

    // Phase 1: Collect all queued events into batch
    struct net_buf *buf;
    while (batch_count < HCI_EVENT_BATCH_SIZE && (buf = rx_queue_get()) != NULL) {
        batch[batch_count++] = buf;
    }

    // Phase 2: Sort batch by priority (connection events before disconnect)
    if (batch_count > 1) {
        hci_event_sort_batch(batch, batch_count);
    }

    // Phase 3: Process sorted batch
    for (int i = 0; i < batch_count; i++) {
        buf = batch[i];
        int ret = recv_cb(hci_dev, buf);
        if (ret < 0) {
            error_printf("recv_cb failed: %d\n", ret);
            net_buf_unref(buf);
        }
    }

    // Phase 4: Process work queue to trigger rx_work (connection callbacks etc)
    // Only the outermost call (depth==0) should process work to prevent re-entrancy.
    // Nested calls via k_sem_take→hci_uart_wfi→mp_bluetooth_zephyr_port_run_task skip work processing.
    extern volatile int mp_bluetooth_zephyr_hci_processing_depth;
    if (batch_count > 0 && mp_bluetooth_zephyr_hci_processing_depth == 0) {
        mp_bluetooth_zephyr_hci_processing_depth++;
        mp_bluetooth_zephyr_work_process();
        mp_bluetooth_zephyr_hci_processing_depth--;
    }

    // Check buffer availability before reading from IPCC.
    // If no buffers available, process work queue to free some.
    // This prevents silent packet drops when buffer pool is exhausted (Issue #12).
    if (!mp_bluetooth_zephyr_buffers_available()) {
        mp_bluetooth_zephyr_work_process();
        if (!mp_bluetooth_zephyr_buffers_available()) {
            // Still no buffers - skip reading, will retry on next poll
            mp_bluetooth_zephyr_port_poll_in_ms(10);
            return;
        }
    }

    // Read HCI packets using port's transport abstraction
    // This works for both UART and IPCC (STM32WB)
    // Note: On STM32WB, this is called from IPCC interrupt so packets
    // are queued rather than processed immediately
    while (mp_bluetooth_hci_uart_readpacket(h4_uart_byte_callback) > 0) {
        // Keep reading while packets are available
        // Re-check buffer availability after each packet to prevent exhaustion
        if (!mp_bluetooth_zephyr_buffers_available()) {
            mp_bluetooth_zephyr_work_process();
            if (!mp_bluetooth_zephyr_buffers_available()) {
                // No buffers - stop reading, will retry on next poll
                mp_bluetooth_zephyr_port_poll_in_ms(10);
                break;
            }
        }
    }
}

// Called by k_sem_take() to process HCI packets while waiting
// This is critical for preventing deadlocks when waiting for HCI command responses
// See docs/BLE_TIMING_ARCHITECTURE.md for detailed timing analysis
void mp_bluetooth_zephyr_hci_uart_wfi(void) {
    if (recv_cb == NULL) {
        return;
    }

    // ARCHITECTURAL FIX for regression introduced in commit 6bdcbeb9ef:
    // Connection events were not being received because mp_bluetooth_zephyr_port_run_task()
    // was removed from this function. Restoring it fixes connection event reception.
    //
    // mp_bluetooth_zephyr_port_run_task() calls mp_bluetooth_zephyr_poll() which is CRITICAL
    // for proper HCI event processing. It must be called BEFORE processing buffers.
    mp_bluetooth_zephyr_port_run_task(NULL);

    // Process any remaining queued RX buffers directly
    // This handles buffers that arrived after mp_bluetooth_zephyr_port_run_task() completed
    // Apply same batching/sorting as mp_bluetooth_zephyr_port_run_task for consistency
    struct net_buf *wfi_batch[HCI_EVENT_BATCH_SIZE];
    int wfi_batch_count = 0;
    struct net_buf *buf;
    while (wfi_batch_count < HCI_EVENT_BATCH_SIZE && (buf = rx_queue_get()) != NULL) {
        wfi_batch[wfi_batch_count++] = buf;
    }

    // CRITICAL: Process work queue BEFORE processing events
    // This ensures connection callbacks fire before disconnect events are processed
    if (wfi_batch_count > 0) {
        mp_bluetooth_zephyr_poll();
    }

    // Sort batch by priority (connection events before disconnect)
    if (wfi_batch_count > 1) {
        hci_event_sort_batch(wfi_batch, wfi_batch_count);
    }

    // Process events
    for (int i = 0; i < wfi_batch_count; i++) {
        recv_cb(hci_dev, wfi_batch[i]);
    }

    // Process work queue after wfi events (same pattern as mp_bluetooth_zephyr_port_run_task)
    // Only outermost call processes work to prevent re-entrancy
    extern volatile int mp_bluetooth_zephyr_hci_processing_depth;
    if (wfi_batch_count > 0 && mp_bluetooth_zephyr_hci_processing_depth == 0) {
        mp_bluetooth_zephyr_hci_processing_depth++;
        mp_bluetooth_zephyr_work_process();
        mp_bluetooth_zephyr_hci_processing_depth--;
    }

    // Give IPCC hardware minimal time to complete any ongoing transfers
    // 100μs is sufficient for hardware without introducing significant latency
    mp_hal_delay_us(100);
}

// Stack monitoring helper
static inline uint32_t get_msp(void) {
    uint32_t result;
    __asm volatile ("MRS %0, msp" : "=r" (result));
    return result;
}

// Zephyr HCI driver implementation

static int hci_stm32_open(const struct device *dev, bt_hci_recv_t recv) {
    DEBUG_HCI_printf("hci_stm32_open\n");

    hci_dev = dev;
    recv_cb = recv;

    // Initialise shared H:4 parser and register recv callback
    mp_bluetooth_zephyr_h4_init(dev, recv);

    // Initialize HCI transport (UART or IPCC)
    int ret = bt_hci_transport_setup(dev);
    if (ret < 0) {
        error_printf("bt_hci_transport_setup failed: %d\n", ret);
        return ret;
    }

    // Start the soft timer to begin periodic work queue processing
    // This starts the timer which will fire and process work queues + HCI packets
    mp_bluetooth_zephyr_port_poll_in_ms(128);  // Start timer with 128ms delay

    return 0;
}

static int hci_stm32_close(const struct device *dev) {
    DEBUG_HCI_printf("hci_stm32_close\n");

    recv_cb = NULL;
    mp_bluetooth_zephyr_h4_deinit();
    mp_bluetooth_zephyr_poll_stop_timer();

    // Teardown HCI transport
    return bt_hci_transport_teardown(dev);
}

static int hci_stm32_send(const struct device *dev, struct net_buf *buf) {
    (void)dev;

    // Map Zephyr buffer type to H:4 packet type
    uint8_t h4_type;
    #pragma GCC diagnostic push
    #pragma GCC diagnostic ignored "-Wdeprecated-declarations"
    switch (bt_buf_get_type(buf)) {
        case BT_BUF_CMD:
            h4_type = H4_CMD;
            break;
        case BT_BUF_ACL_OUT:
            h4_type = H4_ACL;
            break;
        default:
            error_printf("Unknown buffer type: %u\n", bt_buf_get_type(buf));
            net_buf_unref(buf);
            return -1;
    }
    #pragma GCC diagnostic pop

    // Build H:4 packet: [type] + data
    size_t total_len = 1 + buf->len;
    uint8_t *h4_packet = m_new(uint8_t, total_len);

    h4_packet[0] = h4_type;
    memcpy(&h4_packet[1], buf->data, buf->len);

    // Trace HCI commands being sent (only when debug enabled)
    #if ZEPHYR_BLE_DEBUG
    if (h4_type == 0x01 && buf->len >= 3) {  // HCI Command
        uint16_t opcode = buf->data[0] | (buf->data[1] << 8);
        uint8_t param_len = buf->data[2];
        DEBUG_HCI_printf("[SEND] HCI Command: opcode=0x%04x param_len=%u\n", opcode, param_len);
    } else if (h4_type == 0x02 && buf->len >= 9) {  // ACL data
        // ACL header: handle(2) + length(2) = 4 bytes
        // L2CAP header: length(2) + CID(2) = 4 bytes
        // ATT data starts at offset 8
        uint16_t handle = (buf->data[0] | (buf->data[1] << 8)) & 0x0FFF;
        uint16_t acl_len = buf->data[2] | (buf->data[3] << 8);
        uint16_t l2cap_len = buf->data[4] | (buf->data[5] << 8);
        uint16_t l2cap_cid = buf->data[6] | (buf->data[7] << 8);
        uint8_t att_opcode = buf->data[8];
        DEBUG_HCI_printf("[SEND] ACL: handle=0x%03x acl_len=%d l2cap_len=%d cid=0x%04x att_op=0x%02x\n",
            handle, acl_len, l2cap_len, l2cap_cid, att_opcode);
        // Hex dump first 16 bytes
        int count = (buf->len > 16 ? 16 : buf->len);
        mp_printf(&mp_plat_print, "[SEND] HEX:");
        for (int i = 0; i < count; i++) {
            mp_printf(&mp_plat_print, " %02x", buf->data[i]);
        }
        mp_printf(&mp_plat_print, " [done %d][A] t=%lu\n", count, (unsigned long)mp_hal_ticks_ms());
    } else {
        DEBUG_HCI_printf("[SEND] type=0x%02x len=%u\n", h4_type, (unsigned int)total_len);
    }

    DEBUG_HCI_printf("HCI_SEND: uart_write len=%u h4=%02x t=%lu\n", (unsigned)total_len, h4_packet[0], (unsigned long)mp_hal_ticks_ms());
    #endif

    // Send via port's transport abstraction (UART or IPCC)
    int ret = mp_bluetooth_hci_uart_write(h4_packet, total_len);

    m_del(uint8_t, h4_packet, total_len);
    net_buf_unref(buf);

    return ret;
}

// HCI driver API structure
static const struct bt_hci_driver_api hci_stm32_api = {
    .open = hci_stm32_open,
    .close = hci_stm32_close,
    .send = hci_stm32_send,
};

// Device state (must be persistent and zero-initialized)
static struct device_state hci_device_state = {
    .init_res = 0,       // Success
    .initialized = true, // Mark as initialized
};

// HCI device structure (referenced by Zephyr via DEVICE_DT_GET macro)
// Define with the name that DEVICE_DT_GET expects: __device_dts_ord_0
// This is what DEVICE_DT_GET(0) expands to when DT_CHOSEN returns 0
// Use externally_visible to prevent linker garbage collection
__attribute__((used, externally_visible))
const struct device __device_dts_ord_0 = {
    .name = "HCI_STM32",
    .config = NULL,              // No config needed
    .api = &hci_stm32_api,
    .state = &hci_device_state,  // Required for device_is_ready()
    .data = NULL,
    .ops = {.init = NULL},       // No init function needed
    .flags = 0,                  // No special flags
};

// Provide accessible name for port code
__attribute__((used, externally_visible))
const struct device *const mp_bluetooth_zephyr_hci_dev = &__device_dts_ord_0;


// HCI transport setup (called by BLE host during initialization)
// Use __attribute__((used)) to prevent linker garbage collection
__attribute__((used))
int bt_hci_transport_setup(const struct device *dev) {
    (void)dev;

    #if defined(STM32WB)
    // STM32WB: IPCC transport, no external controller
    // rfcore_ble_init() is called by mp_bluetooth_hci_uart_init()
    return mp_bluetooth_hci_uart_init(MICROPY_HW_BLE_UART_ID, MICROPY_HW_BLE_UART_BAUDRATE);

    #else
    // Other STM32: UART transport with external controller
    // Initialize external BLE controller
    int ret = mp_bluetooth_hci_controller_init();
    if (ret != 0) {
        error_printf("Controller init failed: %d\n", ret);
        return ret;
    }

    // Initialize UART HCI transport
    return mp_bluetooth_hci_uart_init(MICROPY_HW_BLE_UART_ID, MICROPY_HW_BLE_UART_BAUDRATE);
    #endif
}

// HCI transport teardown
__attribute__((used))
int bt_hci_transport_teardown(const struct device *dev) {
    (void)dev;
    DEBUG_HCI_printf("bt_hci_transport_teardown\n");

    #if !defined(STM32WB)
    mp_bluetooth_hci_controller_deinit();
    #endif

    return mp_bluetooth_hci_uart_deinit();
}

// Main polling function (called by mpbthciport.c via mp_bluetooth_hci_poll)
void mp_bluetooth_hci_poll(void) {
    // Call mp_bluetooth_zephyr_port_run_task directly to process HCI events
    // This includes: mp_bluetooth_zephyr_poll(), RX queue processing,
    // work queue processing, and reading HCI packets from transport
    mp_bluetooth_zephyr_port_run_task(NULL);

    // Schedule next poll if stack is active
    mp_bluetooth_zephyr_port_poll_in_ms(128);
}

// Initialize Zephyr port (called early in initialization)
void mp_bluetooth_zephyr_port_init(void) {
    DEBUG_HCI_printf("[INIT] mp_bluetooth_zephyr_port_init CALLED\n");

    // Force linker to keep __device_dts_ord_0 by referencing it
    // This prevents garbage collection with --gc-sections
    // Use volatile to prevent compiler from optimizing away the reference
    extern const struct device __device_dts_ord_0;
    volatile const void *keep_device = &__device_dts_ord_0;
    (void)keep_device;

    // Initialise shared soft timer for periodic HCI polling
    mp_bluetooth_zephyr_poll_init_timer();
    DEBUG_HCI_printf("[INIT] soft_timer_static_init completed\n");
}

// Debug wrapper for hci_core.c to print device info
// This avoids including MicroPython headers in Zephyr source files
void mp_bluetooth_zephyr_debug_device(const struct device *dev) {
    #if ZEPHYR_BLE_DEBUG
    DEBUG_HCI_printf("[DEBUG hci_core.c] bt_dev.hci = %p\n", dev);
    if (dev) {
        DEBUG_HCI_printf("[DEBUG hci_core.c]   name = %s\n", dev->name);
        DEBUG_HCI_printf("[DEBUG hci_core.c]   state = %p\n", dev->state);
        if (dev->state) {
            DEBUG_HCI_printf("[DEBUG hci_core.c]     initialized = %d\n", dev->state->initialized);
            DEBUG_HCI_printf("[DEBUG hci_core.c]     init_res = %d\n", dev->state->init_res);
        }
    }
    #else
    (void)dev;
    #endif
}

// Non-inline version of mp_bluetooth_hci_poll_now for extmod code.
// modbluetooth_zephyr.c uses extern declaration, so we need a linkable symbol.
void mp_bluetooth_hci_poll_now(void) {
    mp_bluetooth_hci_poll_now_default();
}

// Port deinit - called during mp_bluetooth_deinit()
void mp_bluetooth_zephyr_port_deinit(void) {
    // Clear any partial H:4 parse state
    mp_bluetooth_zephyr_h4_reset();

    // Clean up shared soft timer and sched_node
    mp_bluetooth_zephyr_poll_cleanup();

    // Reset GATT memory pool for next init cycle (if using bump allocator)
    #if MICROPY_BLUETOOTH_ZEPHYR_GATT_POOL
    mp_bluetooth_zephyr_gatt_pool_reset();
    #endif
}

#endif // MICROPY_PY_BLUETOOTH && MICROPY_BLUETOOTH_ZEPHYR
