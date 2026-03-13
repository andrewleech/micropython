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

// Buffer for incoming HCI packets (4-byte CYW43 header + max HCI packet)
#define CYW43_HCI_HEADER_SIZE 4
#define HCI_MAX_PACKET_SIZE 1024
// IMPORTANT: Must be 4-byte aligned for CYW43 SPI DMA transfers
static uint8_t __attribute__((aligned(4))) hci_rx_buffer[CYW43_HCI_HEADER_SIZE + HCI_MAX_PACKET_SIZE];

// ============================================================================
// HCI Packet Processing (common to FreeRTOS and polling paths)
// ============================================================================

// HCI event type counters (for debugging what packets are received)
static volatile uint32_t hci_rx_evt_cmd_complete = 0;  // 0x0E
static volatile uint32_t hci_rx_evt_cmd_status = 0;    // 0x0F
static volatile uint32_t hci_rx_evt_le_meta = 0;       // 0x3E (LE events including ADV_REPORT)
static volatile uint32_t hci_rx_evt_le_adv_report = 0; // 0x3E subevent 0x02 (advertising reports)
static volatile uint32_t hci_rx_evt_le_conn_complete = 0; // 0x3E subevent 0x01 (LE Connection Complete)
static volatile uint32_t hci_rx_evt_le_enh_conn_complete = 0; // 0x3E subevent 0x0A (LE Enhanced Connection Complete)
static volatile uint32_t hci_rx_evt_disconnect_complete = 0;  // 0x05 (Disconnection Complete)
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
// Used by both FreeRTOS HCI queue processing and polling fallback
static void process_hci_rx_packet(uint8_t *rx_buf, uint32_t len);

// ============================================================================
// FreeRTOS HCI RX Task (Phase 6)
// Uses dedicated task for HCI packet reception when enabled.
// When disabled, uses cooperative polling via soft timer.
// ============================================================================

#include "extmod/zephyr_ble/hal/zephyr_ble_poll.h"
#include "extmod/zephyr_ble/hal/zephyr_ble_port.h"

void mp_bluetooth_zephyr_process_hci_queue(void) {
    // No queue in polling mode - HCI packets processed directly
}

// ============================================================================

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
                    if (pkt_len >= 3) {
                        uint8_t subevent = pkt_data[2];
                        if (subevent == 0x02) {
                            hci_rx_evt_le_adv_report++;
                        } else if (subevent == 0x01) {
                            // LE Connection Complete
                            hci_rx_evt_le_conn_complete++;
                        } else if (subevent == 0x0A) {
                            // LE Enhanced Connection Complete
                            hci_rx_evt_le_enh_conn_complete++;
                        }
                    }
                } else if (evt_code == 0x05) {
                    // Disconnection Complete
                    hci_rx_evt_disconnect_complete++;
                    hci_rx_evt_other++;
                    valid_event = true;
                } else if (evt_code == 0x08 ||
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
        case BT_HCI_H4_ACL: {
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
        }
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

// HCI packet reception handler - called from shared sched_node via soft timer.
// Strong override of weak default in zephyr_ble_poll.c.
void mp_bluetooth_zephyr_port_run_task(mp_sched_node_t *node) {
    (void)node;

    // Early exit if BLE is not active (recv_cb is set by hci_cyw43_open)
    // This prevents processing stale Zephyr state after soft reset.
    // Also skip during deinit to prevent CYW43 SPI reads on a post-reset controller.
    if (recv_cb == NULL || mp_bluetooth_zephyr_shutting_down) {
        return;
    }

    // Process Zephyr BLE work queues and semaphores
    mp_bluetooth_zephyr_poll();

    // Read directly from CYW43 via shared SPI bus.
    // Read and process HCI packets one at a time with work between each.
    extern int cyw43_bluetooth_hci_read(uint8_t *buf, uint32_t max_size, uint32_t *len);
    extern void mp_bluetooth_zephyr_work_process(void);

    while (1) {
        // Check buffer availability before reading HCI data.
        if (!mp_bluetooth_zephyr_buffers_available()) {
            mp_bluetooth_zephyr_work_process();
            if (!mp_bluetooth_zephyr_buffers_available()) {
                // Still no buffers — stop reading, retry next poll.
                break;
            }
        }

        uint32_t len = 0;
        int ret = cyw43_bluetooth_hci_read(hci_rx_buffer, sizeof(hci_rx_buffer), &len);
        if (ret != 0 || len <= CYW43_HCI_HEADER_SIZE) {
            break;  // No more packets.
        }

        process_hci_rx_packet(hci_rx_buffer, len);
        mp_bluetooth_zephyr_work_process();
    }

    // Reschedule soft timer for continuous HCI polling.
    mp_bluetooth_zephyr_port_poll_in_ms(ZEPHYR_BLE_POLL_INTERVAL_MS);
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

    recv_cb = NULL;
    mp_bluetooth_zephyr_poll_stop_timer();

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
    // Clean up shared soft timer and sched_node
    mp_bluetooth_zephyr_poll_cleanup();

    // Clear recv_cb since bt_disable() has reset the controller
    // On reinit, bt_enable() will set up a fresh HCI transport
    recv_cb = NULL;

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
}

void mp_bluetooth_zephyr_poll_uart(void) {
    poll_uart_count++;

    // Skip CYW43 SPI reads during BLE deinit unless we're inside k_sem_take's
    // wait loop (where HCI transport must work for bt_disable's HCI_RESET).
    // After bt_disable sends HCI_RESET, the CYW43 controller enters reset state
    // and SPI reads can hang indefinitely, freezing the Pico W. The soft timer
    // fires run_task() (not in wait loop) which would trigger this hang.
    // k_sem_take calls (in wait loop) are allowed so bt_disable can complete.
    if (mp_bluetooth_zephyr_shutting_down && !mp_bluetooth_zephyr_in_wait_loop) {
        return;
    }

    // Prevent recursion - but allow re-entry from semaphore wait loops
    // When mp_bluetooth_zephyr_in_wait_loop is true, we're in k_sem_take() polling
    // and need to read HCI to receive the response that will signal the semaphore.
    if (poll_uart_in_progress && !mp_bluetooth_zephyr_in_wait_loop) {
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
    poll_uart_cyw43_calls++;

    // Read HCI packets one at a time with work between each.
    // NOTE: This path is only used when HCI RX task is NOT running.
    extern int cyw43_bluetooth_hci_read(uint8_t *buf, uint32_t max_size, uint32_t *len);
    extern void mp_bluetooth_zephyr_work_process(void);
    while (1) {
        // Check buffer availability before reading more HCI data.
        if (!mp_bluetooth_zephyr_buffers_available()) {
            mp_bluetooth_zephyr_work_process();
            if (!mp_bluetooth_zephyr_buffers_available()) {
                break;
            }
        }

        uint32_t len = 0;
        int ret = cyw43_bluetooth_hci_read(hci_rx_buffer, sizeof(hci_rx_buffer), &len);
        if (ret != 0 || len <= CYW43_HCI_HEADER_SIZE) {
            break;  // No more packets.
        }

        poll_uart_hci_reads++;
        process_hci_rx_packet(hci_rx_buffer, len);
        mp_bluetooth_zephyr_work_process();
    }

    poll_uart_in_progress = false;
}

// Strong override: read HCI from CYW43 SPI, process Zephyr work, reschedule.
void mp_bluetooth_hci_poll(void) {
    if (mp_bluetooth_is_active()) {
        mp_bluetooth_zephyr_poll_uart();
        mp_bluetooth_zephyr_poll();
        mp_bluetooth_hci_poll_in_ms(ZEPHYR_BLE_POLL_INTERVAL_MS);
    }
}

// Strong override: read HCI during k_sem_take wait loops.
// Must call poll_uart directly to drain all pending CYW43 packets.
void mp_bluetooth_zephyr_hci_uart_wfi(void) {
    mp_bluetooth_zephyr_poll_uart();
    mp_bluetooth_zephyr_poll();
}

#else // !MICROPY_PY_NETWORK_CYW43

// Stub implementations for ports without CYW43
void mp_bluetooth_zephyr_port_init(void) {
}

void mp_bluetooth_zephyr_port_poll_in_ms(uint32_t ms) {
    (void)ms;
}

#endif // MICROPY_PY_NETWORK_CYW43

// Zephyr kernel arch stubs — interrupt control for cooperative scheduler.
#include "hardware/sync.h"

unsigned int arch_irq_lock(void) {
    return save_and_disable_interrupts();
}

void arch_irq_unlock(unsigned int key) {
    restore_interrupts(key);
}

#endif // MICROPY_PY_BLUETOOTH && MICROPY_BLUETOOTH_ZEPHYR
