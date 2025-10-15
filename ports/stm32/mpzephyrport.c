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
#include "shared/runtime/softtimer.h"
#include "mpbthciport.h"

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

#define debug_printf(...) mp_printf(&mp_plat_print, "mpzephyrport: " __VA_ARGS__)
#define error_printf(...) mp_printf(&mp_plat_print, "mpzephyrport ERROR: " __VA_ARGS__)

// H:4 packet types
#define H4_CMD  0x01
#define H4_ACL  0x02
#define H4_SCO  0x03
#define H4_EVT  0x04

// Zephyr HCI driver callback and device
static const struct device *hci_dev = NULL;
static bt_hci_recv_t recv_cb = NULL;

// Soft timer for scheduling HCI poll (zero-initialized to prevent startup crashes)
static soft_timer_entry_t mp_zephyr_hci_soft_timer = {0};
static mp_sched_node_t mp_zephyr_hci_sched_node = {0};

// Queue for completed HCI packets (received from interrupt context)
// These are deferred for processing in scheduler context to avoid stack overflow
#define RX_QUEUE_SIZE 8
static struct net_buf *rx_queue[RX_QUEUE_SIZE];
static volatile size_t rx_queue_head = 0;
static volatile size_t rx_queue_tail = 0;

// H:4 packet parser state
typedef enum {
    H4_STATE_TYPE,      // Waiting for packet type byte
    H4_STATE_HEADER,    // Reading packet header
    H4_STATE_PAYLOAD,   // Reading packet payload
} h4_state_t;

static h4_state_t h4_state = H4_STATE_TYPE;
static uint8_t h4_type;
static uint8_t h4_header_buf[4];  // Max header size (ACL: 4 bytes)
static size_t h4_header_idx;
static size_t h4_header_len;
static struct net_buf *h4_buf;
static size_t h4_payload_remaining;

// Forward declarations
static void mp_zephyr_hci_poll_now(void);
void mp_bluetooth_zephyr_port_poll_in_ms(uint32_t ms);

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

// Reset H:4 parser state
static void h4_parser_reset(void) {
    h4_state = H4_STATE_TYPE;
    h4_header_idx = 0;
    h4_payload_remaining = 0;
    if (h4_buf) {
        net_buf_unref(h4_buf);
        h4_buf = NULL;
    }
}

// Process one byte through H:4 parser
// Returns true if packet is complete
static bool h4_parser_process_byte(uint8_t byte) {
    switch (h4_state) {
        case H4_STATE_TYPE:
            h4_type = byte;
            h4_header_idx = 0;

            // Determine header length based on packet type
            switch (h4_type) {
                case H4_EVT:
                    h4_header_len = 2;  // opcode + length
                    break;
                case H4_ACL:
                    h4_header_len = 4;  // handle(2) + length(2)
                    break;
                default:
                    error_printf("Unknown H:4 packet type: 0x%02x\n", h4_type);
                    h4_parser_reset();
                    return false;
            }

            h4_state = H4_STATE_HEADER;
            return false;

        case H4_STATE_HEADER:
            h4_header_buf[h4_header_idx++] = byte;

            if (h4_header_idx >= h4_header_len) {
                // Header complete, determine payload length
                size_t payload_len;

                switch (h4_type) {
                    case H4_EVT:
                        payload_len = h4_header_buf[1];  // length byte

                        // Allocate net_buf for event
                        h4_buf = bt_buf_get_evt(h4_header_buf[0], false, K_NO_WAIT);
                        if (!h4_buf) {
                            error_printf("Failed to allocate event buffer\n");
                            h4_parser_reset();
                            return false;
                        }

                        // Add header to buffer
                        net_buf_add_mem(h4_buf, h4_header_buf, h4_header_len);
                        break;

                    case H4_ACL:
                        payload_len = h4_header_buf[2] | (h4_header_buf[3] << 8);

                        // Allocate net_buf for ACL
                        h4_buf = bt_buf_get_rx(BT_BUF_ACL_IN, K_NO_WAIT);
                        if (!h4_buf) {
                            error_printf("Failed to allocate ACL buffer\n");
                            h4_parser_reset();
                            return false;
                        }

                        // Add header to buffer
                        net_buf_add_mem(h4_buf, h4_header_buf, h4_header_len);
                        break;

                    default:
                        h4_parser_reset();
                        return false;
                }

                if (payload_len == 0) {
                    // No payload, packet is complete
                    h4_state = H4_STATE_TYPE;
                    return true;
                } else {
                    // Transition to payload state
                    h4_payload_remaining = payload_len;
                    h4_state = H4_STATE_PAYLOAD;
                }
            }
            return false;

        case H4_STATE_PAYLOAD:
            if (!h4_buf) {
                error_printf("No buffer in payload state\n");
                h4_parser_reset();
                return false;
            }

            net_buf_add_u8(h4_buf, byte);
            h4_payload_remaining--;

            if (h4_payload_remaining == 0) {
                // Payload complete, packet is ready
                h4_state = H4_STATE_TYPE;
                return true;
            }
            return false;
    }

    return false;
}

// Callback for mp_bluetooth_hci_uart_readpacket() - called for each byte
// IMPORTANT: This may be called from interrupt context (IPCC IRQ on STM32WB)
// DO NOT call recv_cb() directly - queue the buffer for processing in scheduler context
static void h4_uart_byte_callback(uint8_t byte) {
    if (h4_parser_process_byte(byte)) {
        if (h4_buf) {
            struct net_buf *buf = h4_buf;
            h4_buf = NULL;  // Ownership transferred

            // Queue the buffer for processing in scheduler context
            // This avoids calling bt_hci_recv() from interrupt context
            if (!rx_queue_put(buf)) {
                error_printf("RX queue full\n");
                net_buf_unref(buf);
            } else {
                // DO NOT add trace output here - this runs in interrupt context!
                // Schedule task to process queued packets
                // This is safe from IRQ context (same as NimBLE UART IRQ)
                mp_zephyr_hci_poll_now();
            }
        }
    }
}

// This is called by soft_timer and executes at PendSV/scheduler level
static void mp_zephyr_hci_soft_timer_callback(soft_timer_entry_t *self) {
    mp_zephyr_hci_poll_now();
}

// HCI packet reception handler - called when data arrives
static void run_zephyr_hci_task(mp_sched_node_t *node) {
    (void)node;

    // Process Zephyr BLE work queues and semaphores
    mp_bluetooth_zephyr_poll();

    if (recv_cb == NULL) {
        return;
    }

    // Process any queued RX buffers (from interrupt context)
    struct net_buf *buf;
    while ((buf = rx_queue_get()) != NULL) {
        int ret = recv_cb(hci_dev, buf);
        if (ret < 0) {
            error_printf("recv_cb failed: %d\n", ret);
            net_buf_unref(buf);
        }
    }

    // Read HCI packets using port's transport abstraction
    // This works for both UART and IPCC (STM32WB)
    // Note: On STM32WB, this is called from IPCC interrupt so packets
    // are queued rather than processed immediately
    while (mp_bluetooth_hci_uart_readpacket(h4_uart_byte_callback) > 0) {
        // Keep reading while packets are available
    }
}

static void mp_zephyr_hci_poll_now(void) {
    mp_sched_schedule_node(&mp_zephyr_hci_sched_node, run_zephyr_hci_task);
}

// Called by k_sem_take() to process HCI packets while waiting
// This is critical for preventing deadlocks when waiting for HCI command responses
void mp_bluetooth_zephyr_hci_uart_wfi(void) {
    if (recv_cb == NULL) {
        return;
    }

    // Process Zephyr work queues (for timers, delayed work, etc)
    mp_bluetooth_zephyr_work_process();

    // CRITICAL: Run any pending scheduled tasks (e.g., from IPCC interrupt)
    // The IPCC interrupt calls mp_bluetooth_hci_poll_now() which schedules a task
    // that reads the HCI response. We need to run that task!
    // mp_event_wait_ms(0) runs scheduled tasks and returns immediately
    mp_event_wait_ms(1);

    // Check for HCI data that may have arrived already
    mp_bluetooth_hci_uart_readpacket(h4_uart_byte_callback);

    // Deliver any queued RX buffers
    struct net_buf *buf;
    while ((buf = rx_queue_get()) != NULL) {
        recv_cb(hci_dev, buf);
    }
}

// Zephyr HCI driver implementation

static int hci_stm32_open(const struct device *dev, bt_hci_recv_t recv) {
    debug_printf("hci_stm32_open\n");

    hci_dev = dev;
    recv_cb = recv;

    // Reset H:4 parser
    h4_parser_reset();

    // Initialize HCI transport (UART or IPCC)
    int ret = bt_hci_transport_setup(dev);
    if (ret < 0) {
        error_printf("bt_hci_transport_setup failed: %d\n", ret);
        return ret;
    }

    // Start polling for incoming HCI packets immediately
    // This is needed for HCI command responses during bt_enable()
    mp_zephyr_hci_poll_now();

    return 0;
}

static int hci_stm32_close(const struct device *dev) {
    debug_printf("hci_stm32_close\n");

    recv_cb = NULL;
    h4_parser_reset();
    soft_timer_remove(&mp_zephyr_hci_soft_timer);

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

    // Trace HCI commands being sent
    mp_printf(&mp_plat_print, "[S] HCI type=0x%02x len=%u\n", h4_type, (unsigned int)total_len);

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
    debug_printf("bt_hci_transport_setup\n");

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
    debug_printf("bt_hci_transport_teardown\n");

    #if !defined(STM32WB)
    mp_bluetooth_hci_controller_deinit();
    #endif

    return mp_bluetooth_hci_uart_deinit();
}

// Main polling function (called by mpbthciport.c via mp_bluetooth_hci_poll)
void mp_bluetooth_hci_poll(void) {
    // Process Zephyr work queues and semaphores
    mp_bluetooth_zephyr_poll();

    // Schedule next poll if stack is active
    // The soft timer will re-trigger run_zephyr_hci_task
    mp_bluetooth_zephyr_port_poll_in_ms(128);
}

// Initialize Zephyr port (called early in initialization)
void mp_bluetooth_zephyr_port_init(void) {
    debug_printf("mp_bluetooth_zephyr_port_init: ENTER\n");

    // Force linker to keep __device_dts_ord_0 by referencing it
    // This prevents garbage collection with --gc-sections
    // Use volatile to prevent compiler from optimizing away the reference
    extern const struct device __device_dts_ord_0;
    volatile const void *keep_device = &__device_dts_ord_0;
    (void)keep_device;

    debug_printf("mp_bluetooth_zephyr_port_init: Initializing soft timer\n");
    soft_timer_static_init(
        &mp_zephyr_hci_soft_timer,
        SOFT_TIMER_MODE_ONE_SHOT,
        0,
        mp_zephyr_hci_soft_timer_callback
        );

    debug_printf("mp_bluetooth_zephyr_port_init: EXIT\n");
}

// Schedule HCI poll in N milliseconds
void mp_bluetooth_zephyr_port_poll_in_ms(uint32_t ms) {
    soft_timer_reinsert(&mp_zephyr_hci_soft_timer, ms);
}

// Debug wrapper for hci_core.c to print device info
// This avoids including MicroPython headers in Zephyr source files
void mp_bluetooth_zephyr_debug_device(const struct device *dev) {
    mp_printf(&mp_plat_print, "[DEBUG hci_core.c] bt_dev.hci = %p\n", dev);
    if (dev) {
        mp_printf(&mp_plat_print, "[DEBUG hci_core.c]   name = %s\n", dev->name);
        mp_printf(&mp_plat_print, "[DEBUG hci_core.c]   state = %p\n", dev->state);
        if (dev->state) {
            mp_printf(&mp_plat_print, "[DEBUG hci_core.c]     initialized = %d\n", dev->state->initialized);
            mp_printf(&mp_plat_print, "[DEBUG hci_core.c]     init_res = %d\n", dev->state->init_res);
        }
    }
}

#endif // MICROPY_PY_BLUETOOTH && MICROPY_BLUETOOTH_ZEPHYR
