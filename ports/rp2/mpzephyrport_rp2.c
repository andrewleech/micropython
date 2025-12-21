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
#endif

// Debug output controlled by ZEPHYR_BLE_DEBUG
// Note: debug_printf is NOT thread-safe. Only call from main task context.
// For HCI RX task on core1, use debug_printf_hci_task which is disabled.
#if ZEPHYR_BLE_DEBUG
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
static uint8_t hci_rx_buffer[CYW43_HCI_HEADER_SIZE + HCI_MAX_PACKET_SIZE];

// ============================================================================
// FreeRTOS HCI RX Task (Phase 6)
// ============================================================================
#if MICROPY_PY_THREAD
#include "FreeRTOS.h"
#include "task.h"

#define HCI_RX_TASK_STACK_SIZE 1024  // 4KB (in words)
#define HCI_RX_TASK_PRIORITY (configMAX_PRIORITIES - 1)

static TaskHandle_t hci_rx_task_handle = NULL;
static volatile bool hci_rx_task_running = false;      // Signal task to stop
static volatile bool hci_rx_task_started = false;      // Task has started and is ready
static volatile bool hci_rx_task_exited = false;       // Task has exited
static StaticTask_t hci_rx_task_tcb;
static StackType_t hci_rx_task_stack[HCI_RX_TASK_STACK_SIZE];

// Separate buffer for HCI RX task to avoid race with polling code
static uint8_t hci_rx_task_buffer[CYW43_HCI_HEADER_SIZE + HCI_MAX_PACKET_SIZE];

// Process a single HCI packet from the given buffer
static void process_hci_rx_packet(uint8_t *rx_buf, uint32_t len) {
    if (recv_cb == NULL || len <= CYW43_HCI_HEADER_SIZE) {
        return;
    }

    // Extract packet type from CYW43 header (byte 3)
    uint8_t pkt_type = rx_buf[3];
    uint8_t *pkt_data = &rx_buf[CYW43_HCI_HEADER_SIZE];
    uint32_t pkt_len = len - CYW43_HCI_HEADER_SIZE;

    // Allocate Zephyr net_buf based on packet type
    struct net_buf *buf = NULL;

    switch (pkt_type) {
        case BT_HCI_H4_EVT:
            if (pkt_len >= 2) {
                buf = bt_buf_get_evt(pkt_data[0], false, K_NO_WAIT);
            }
            break;
        case BT_HCI_H4_ACL:
            buf = bt_buf_get_rx(BT_BUF_ACL_IN, K_NO_WAIT);
            break;
        default:
            debug_printf("HCI RX: unknown packet type 0x%02x\n", pkt_type);
            return;
    }

    if (buf == NULL) {
        error_printf("HCI RX: failed to allocate buffer for type 0x%02x\n", pkt_type);
        return;
    }

    // Copy packet data to net_buf and deliver to Zephyr
    net_buf_add_mem(buf, pkt_data, pkt_len);
    int ret = recv_cb(hci_dev, buf);
    if (ret < 0) {
        debug_printf("HCI RX: recv_cb failed %d\n", ret);
        net_buf_unref(buf);
    }
}

// HCI RX task - runs continuously, polls CYW43 for incoming HCI data
// NOTE: This runs on core1, don't use debug_printf (printf race condition)
static void hci_rx_task_func(void *arg) {
    (void)arg;
    debug_printf_hci_task("HCI RX task started\n");

    extern int cyw43_bluetooth_hci_read(uint8_t *buf, uint32_t max_size, uint32_t *len);

    // Signal that we've started and are ready
    hci_rx_task_started = true;

    while (hci_rx_task_running) {
        if (recv_cb != NULL) {
            uint32_t len = 0;
            // Use task-local buffer to avoid race with polling code
            int ret = cyw43_bluetooth_hci_read(hci_rx_task_buffer, sizeof(hci_rx_task_buffer), &len);

            if (ret == 0 && len > CYW43_HCI_HEADER_SIZE) {
                process_hci_rx_packet(hci_rx_task_buffer, len);
            }
        }

        // Yield to other tasks - 1ms poll interval
        vTaskDelay(pdMS_TO_TICKS(1));
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

    hci_rx_task_handle = xTaskCreateStatic(
        hci_rx_task_func,
        "hci_rx",
        HCI_RX_TASK_STACK_SIZE,
        NULL,
        HCI_RX_TASK_PRIORITY,
        hci_rx_task_stack,
        &hci_rx_task_tcb
        );

    if (hci_rx_task_handle == NULL) {
        error_printf("Failed to create HCI RX task\n");
        hci_rx_task_running = false;
    }
}

// Stop HCI RX task - called during BLE deinitialization
void mp_bluetooth_zephyr_hci_rx_task_stop(void) {
    if (hci_rx_task_handle == NULL) {
        return;
    }

    debug_printf("Stopping HCI RX task\n");

    // Signal task to exit
    hci_rx_task_running = false;

    // Wait for task to signal exit (avoids undefined behavior of eTaskGetState after vTaskDelete)
    for (int i = 0; i < 100 && !hci_rx_task_exited; i++) {  // 100 * 10ms = 1s max
        vTaskDelay(pdMS_TO_TICKS(10));
    }

    hci_rx_task_handle = NULL;
    debug_printf("HCI RX task stopped\n");
}

// Check if HCI RX task is active and ready
bool mp_bluetooth_zephyr_hci_rx_task_active(void) {
    // Only return true once task has fully started and is processing HCI data
    return hci_rx_task_handle != NULL && hci_rx_task_running && hci_rx_task_started;
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

#endif // MICROPY_PY_THREAD
// ============================================================================

// Forward declarations
static void mp_zephyr_hci_poll_now(void);

// This is called by soft_timer and executes at PendSV level
static void mp_zephyr_hci_soft_timer_callback(soft_timer_entry_t *self) {
    mp_zephyr_hci_poll_now();
}

// HCI packet reception handler - called when data arrives from CYW43 SPI
static void run_zephyr_hci_task(mp_sched_node_t *node) {
    (void)node;

    // Process Zephyr BLE work queues and semaphores
    mp_bluetooth_zephyr_poll();

    if (recv_cb == NULL) {
        return;
    }

    // Skip HCI reading if dedicated HCI RX task is active
    // The task handles HCI reception with its own buffer
    #if MICROPY_PY_THREAD
    if (mp_bluetooth_zephyr_hci_rx_task_active()) {
        return;
    }
    #endif

    // Read from CYW43 via shared SPI bus
    extern int cyw43_bluetooth_hci_read(uint8_t *buf, uint32_t max_size, uint32_t *len);
    uint32_t len = 0;
    int ret = cyw43_bluetooth_hci_read(hci_rx_buffer, sizeof(hci_rx_buffer), &len);

    if (ret != 0 || len <= CYW43_HCI_HEADER_SIZE) {
        return; // No data or error
    }

    // Extract packet type from CYW43 header (byte 3)
    uint8_t pkt_type = hci_rx_buffer[3];
    uint8_t *pkt_data = &hci_rx_buffer[CYW43_HCI_HEADER_SIZE];
    uint32_t pkt_len = len - CYW43_HCI_HEADER_SIZE;

    // Allocate Zephyr net_buf based on packet type
    struct net_buf *buf = NULL;

    switch (pkt_type) {
        case BT_HCI_H4_EVT:
            if (pkt_len >= 2) {
                buf = bt_buf_get_evt(pkt_data[0], false, K_NO_WAIT);
            }
            break;
        case BT_HCI_H4_ACL:
            buf = bt_buf_get_rx(BT_BUF_ACL_IN, K_NO_WAIT);
            break;
        default:
            error_printf("Unknown HCI packet type: 0x%02x\n", pkt_type);
            return;
    }

    if (!buf) {
        error_printf("Failed to allocate buffer for HCI packet\n");
        return;
    }

    // Copy packet data to net_buf
    net_buf_add_mem(buf, pkt_data, pkt_len);

    // Pass buffer to Zephyr BLE stack
    int recv_ret = recv_cb(hci_dev, buf);
    if (recv_ret < 0) {
        error_printf("recv_cb failed: %d\n", recv_ret);
        net_buf_unref(buf);
    }
}

static void mp_zephyr_hci_poll_now(void) {
    // Note: mp_printf can crash during BLE init - avoid debug output here
    mp_sched_schedule_node(&mp_zephyr_hci_sched_node, run_zephyr_hci_task);
}

// Zephyr HCI driver implementation

static int hci_cyw43_open(const struct device *dev, bt_hci_recv_t recv) {
    debug_printf("hci_cyw43_open called, dev=%p recv=%p\n", dev, recv);
    hci_dev = dev;
    recv_cb = recv;

    // CYW43 BT is already initialized via cyw43_bluetooth_hci_init() in bt_hci_transport_setup()
    // Start HCI RX task now that CYW43 is ready and recv_cb is set
    // The task runs independently and handles all HCI reception, signaling semaphores
    #if MICROPY_PY_THREAD
    mp_bluetooth_zephyr_hci_rx_task_start();

    // Wait for HCI RX task to actually start (synchronize with task creation)
    // This ensures k_sem_take() will use true blocking instead of polling
    for (int i = 0; i < 100 && !mp_bluetooth_zephyr_hci_rx_task_active(); i++) {
        vTaskDelay(pdMS_TO_TICKS(1));
    }
    debug_printf("HCI RX task ready: %d\n", mp_bluetooth_zephyr_hci_rx_task_active());
    #endif

    debug_printf("hci_cyw43_open completed\n");
    return 0;
}

static int hci_cyw43_close(const struct device *dev) {
    (void)dev;
    debug_printf("hci_cyw43_close\n");

    // Stop HCI RX task before clearing recv_cb
    #if MICROPY_PY_THREAD
    mp_bluetooth_zephyr_hci_rx_task_stop();
    #endif

    recv_cb = NULL;
    soft_timer_remove(&mp_zephyr_hci_soft_timer);
    return 0;
}

static int hci_cyw43_send(const struct device *dev, struct net_buf *buf) {
    (void)dev;
    debug_printf("hci_cyw43_send: type=%u len=%u\n", bt_buf_get_type(buf), buf->len);

    // Map Zephyr buffer type to H:4 packet type
    uint8_t pkt_type;
    switch (bt_buf_get_type(buf)) {
        case BT_BUF_CMD:
            pkt_type = BT_HCI_H4_CMD;
            break;
        case BT_BUF_ACL_OUT:
            pkt_type = BT_HCI_H4_ACL;
            break;
        default:
            error_printf("Unknown buffer type: %u\n", bt_buf_get_type(buf));
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
    debug_printf("bt_hci_transport_setup called\n");
    (void)dev;

    // Initialize CYW43 BT using shared SPI bus (same as BTstack)
    // This ensures WiFi driver is up first, then loads BT firmware
    debug_printf("Calling cyw43_bluetooth_hci_init\n");
    extern int cyw43_bluetooth_hci_init(void);
    int ret = cyw43_bluetooth_hci_init();
    if (ret != 0) {
        error_printf("cyw43_bluetooth_hci_init failed: %d\n", ret);
        return ret;
    }

    debug_printf("bt_hci_transport_setup completed\n");
    return 0;
}

// HCI transport teardown
int bt_hci_transport_teardown(const struct device *dev) {
    debug_printf("bt_hci_transport_teardown\n");
    (void)dev;

    // TODO: De-initialize CYW43 BT controller
    // Would power down the BT controller here

    return 0;
}

// Initialize Zephyr port
void mp_bluetooth_zephyr_port_init(void) {
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

void mp_bluetooth_zephyr_poll_uart(void) {
    // Prevent recursion
    if (poll_uart_in_progress || recv_cb == NULL) {
        return;
    }

    // Skip if HCI RX task is handling reception
    #if MICROPY_PY_THREAD
    if (mp_bluetooth_zephyr_hci_rx_task_active()) {
        return;
    }
    #endif

    poll_uart_in_progress = true;

    // Read from CYW43 via shared SPI bus
    extern int cyw43_bluetooth_hci_read(uint8_t *buf, uint32_t max_size, uint32_t *len);
    uint32_t len = 0;
    int ret = cyw43_bluetooth_hci_read(hci_rx_buffer, sizeof(hci_rx_buffer), &len);

    if (ret == 0 && len > CYW43_HCI_HEADER_SIZE) {
        // Extract packet type from CYW43 header (byte 3)
        uint8_t pkt_type = hci_rx_buffer[3];
        uint8_t *pkt_data = &hci_rx_buffer[CYW43_HCI_HEADER_SIZE];
        uint32_t pkt_len = len - CYW43_HCI_HEADER_SIZE;

        // Allocate Zephyr net_buf based on packet type
        struct net_buf *buf = NULL;

        switch (pkt_type) {
            case BT_HCI_H4_EVT:
                if (pkt_len >= 2) {
                    buf = bt_buf_get_evt(pkt_data[0], false, K_NO_WAIT);
                }
                break;
            case BT_HCI_H4_ACL:
                buf = bt_buf_get_rx(BT_BUF_ACL_IN, K_NO_WAIT);
                break;
            default:
                break;
        }

        if (buf != NULL) {
            net_buf_add_mem(buf, pkt_data, pkt_len);
            recv_cb(hci_dev, buf);
        }
    }

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
