/*
 * This file is part of the MicroPython project, http://micropython.org/
 *
 * The MIT License (MIT)
 *
 * Copyright (c) 2019-2021 Damien P. George
 * Copyright (c) 2019-2020 Jim Mussared
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

#include "py/runtime.h"
#include "py/mperrno.h"
#include "py/mphal.h"

#include <stdlib.h>  // For malloc() - UUID/GATT memory outside GC heap

#if MICROPY_PY_BLUETOOTH

#include <zephyr/types.h>
#include <zephyr/bluetooth/bluetooth.h>
#include <zephyr/bluetooth/hci.h>
#include <zephyr/bluetooth/conn.h>
#include <zephyr/bluetooth/uuid.h>
#include <zephyr/bluetooth/gatt.h>
#include <zephyr/bluetooth/l2cap.h>
#include <zephyr/sys/byteorder.h>
#include <zephyr/device.h>
#if defined(CONFIG_SETTINGS)
#include <zephyr/settings/settings.h>
#endif
#include "host/att_internal.h"
#include "extmod/modbluetooth.h"
#include "extmod/zephyr_ble/hal/zephyr_ble_work.h"
#include "extmod/zephyr_ble/hal/zephyr_ble_settings.h"
#ifndef __ZEPHYR__
#include "extmod/zephyr_ble/net_buf_pool_registry.h"
#endif

#if MICROPY_PY_BLUETOOTH_USE_ZEPHYR_FREERTOS
#include "FreeRTOS.h"
#include "task.h"
#endif

// Access Zephyr's internal bt_dev for force-reset on deinit failure
// Include path has lib/zephyr/subsys/bluetooth, so use host/ prefix
#include "host/hci_core.h"

#if MICROPY_PY_NETWORK_CYW43
// For cyw43_t definition (bt_loaded reset on deinit failure)
#include "lib/cyw43-driver/src/cyw43.h"
#endif

#include "extmod/zephyr_ble/hal/zephyr_ble_port.h"
#include "extmod/zephyr_ble/hal/zephyr_ble_poll.h"

#if ZEPHYR_BLE_DEBUG
#define DEBUG_printf(...) mp_printf(&mp_plat_print, "BLE: " __VA_ARGS__)
static int debug_seq = 0;
#define DEBUG_SEQ_printf(...) mp_printf(&mp_plat_print, "[%04d] " __VA_ARGS__, ++debug_seq)
static int call_depth = 0;
#define DEBUG_ENTER(name) do { \
    mp_printf(&mp_plat_print, "%*s--> %s\n", call_depth*2, "", name); \
    call_depth++; \
} while(0)
#define DEBUG_EXIT(name) do { \
    call_depth--; \
    mp_printf(&mp_plat_print, "%*s<-- %s\n", call_depth*2, "", name); \
} while(0)
#else
#define DEBUG_printf(...) do {} while (0)
#define DEBUG_SEQ_printf(...) do {} while (0)
#define DEBUG_ENTER(name) do {} while (0)
#define DEBUG_EXIT(name) do {} while (0)
#endif

#define BLE_HCI_SCAN_ITVL_MIN 0x10
#define BLE_HCI_SCAN_ITVL_MAX 0xffff
#define BLE_HCI_SCAN_WINDOW_MIN 0x10
#define BLE_HCI_SCAN_WINDOW_MAX 0xffff

// Map subscribe params to MicroPython notification/indication IRQ event.
// With BT_GATT_SUBSCRIBE_HAS_RECEIVED_OPCODE, uses the actual ATT opcode.
// Without it, infers from the CCCD subscription value.
// BT_GATT_NOTIFY_TYPE_NOTIFY_MULT (multi-handle notification) maps to NOTIFY
// since MicroPython has no separate IRQ for it.
#if defined(BT_GATT_SUBSCRIBE_HAS_RECEIVED_OPCODE)
#define GATTC_NOTIFY_EVENT_TYPE(params) \
    (((params)->received_opcode == BT_GATT_NOTIFY_TYPE_INDICATE) \
        ? MP_BLUETOOTH_IRQ_GATTC_INDICATE \
        : MP_BLUETOOTH_IRQ_GATTC_NOTIFY)
#else
#define GATTC_NOTIFY_EVENT_TYPE(params) \
    (((params)->value & BT_GATT_CCC_INDICATE) \
        ? MP_BLUETOOTH_IRQ_GATTC_INDICATE \
        : MP_BLUETOOTH_IRQ_GATTC_NOTIFY)
#endif

#define ERRNO_BLUETOOTH_NOT_ACTIVE MP_ENODEV

#define MP_BLUETOOTH_ZEPHYR_MAX_SERVICES            (8)

/* This masks Permission bits from GATT API */
#define GATT_PERM_MASK  (BT_GATT_PERM_READ | \
    BT_GATT_PERM_READ_AUTHEN | \
    BT_GATT_PERM_READ_ENCRYPT | \
    BT_GATT_PERM_WRITE | \
    BT_GATT_PERM_WRITE_AUTHEN | \
    BT_GATT_PERM_WRITE_ENCRYPT | \
    BT_GATT_PERM_PREPARE_WRITE)

#define GATT_PERM_ENC_READ_MASK     (BT_GATT_PERM_READ_ENCRYPT | \
    BT_GATT_PERM_READ_AUTHEN)

#define GATT_PERM_ENC_WRITE_MASK    (BT_GATT_PERM_WRITE_ENCRYPT | \
    BT_GATT_PERM_WRITE_AUTHEN)

enum {
    MP_BLUETOOTH_ZEPHYR_BLE_STATE_OFF,
    MP_BLUETOOTH_ZEPHYR_BLE_STATE_ACTIVE,
    MP_BLUETOOTH_ZEPHYR_BLE_STATE_SUSPENDED,
};

enum {
    MP_BLUETOOTH_ZEPHYR_GAP_SCAN_STATE_INACTIVE,
    MP_BLUETOOTH_ZEPHYR_GAP_SCAN_STATE_DEACTIVATING,
    MP_BLUETOOTH_ZEPHYR_GAP_SCAN_STATE_ACTIVE,
};

union uuid_u {
    struct bt_uuid uuid;
    struct bt_uuid_16 u16;
    struct bt_uuid_32 u32;
    struct bt_uuid_128 u128;
};

// Debug watchpoint code removed after UUID corruption fix verified.
// The issue was using m_new() (GC heap) with MP_OBJ_FROM_PTR() for non-object pointers.
// Fixed by using malloc() to keep GATT data outside GC control.
#define debug_check_uuid(where) ((void)0)

// Forward declarations for GATT client functions used elsewhere
#if MICROPY_PY_BLUETOOTH_ENABLE_GATT_CLIENT
static void gattc_register_auto_subscription(struct bt_conn *conn, uint16_t conn_handle, uint16_t value_handle, uint8_t properties);
static void gattc_clear_auto_subscriptions(uint16_t conn_handle);
static void gattc_remove_auto_subscription_for_handle(struct bt_conn *conn, uint16_t conn_handle, uint16_t value_handle);
#endif

struct add_characteristic {
    uint8_t properties;
    uint8_t permissions;
    const struct bt_uuid *uuid;
};

struct add_descriptor {
    uint8_t permissions;
    const struct bt_uuid *uuid;
};

typedef struct _mp_bt_zephyr_conn_t {
    struct bt_conn *conn;
    struct _mp_bt_zephyr_conn_t *next;
} mp_bt_zephyr_conn_t;

#if MICROPY_PY_BLUETOOTH_ENABLE_L2CAP_CHANNELS

// L2CAP RX accumulation buffer size (must hold total expected data per direction)
// The ble_l2cap.py test sends 3640 bytes total, so 4096 gives margin
#define L2CAP_RX_BUF_SIZE 4096

// L2CAP Connection-Oriented Channel structure
typedef struct _mp_bluetooth_zephyr_l2cap_channel_t {
    struct bt_l2cap_le_chan le_chan;   // Zephyr L2CAP LE channel (embedded)
    uint16_t mtu;                      // Our configured MTU
    uint8_t *rx_buf;                   // RX accumulation buffer
    size_t rx_len;                     // Current data length in rx_buf
} mp_bluetooth_zephyr_l2cap_channel_t;

// L2CAP Server structure (for listening)
typedef struct _mp_bluetooth_zephyr_l2cap_server_t {
    struct bt_l2cap_server server;     // Zephyr server structure
    uint16_t mtu;                      // MTU for accepted connections
} mp_bluetooth_zephyr_l2cap_server_t;

// Static L2CAP server structure - persists across soft resets because Zephyr
// has no bt_l2cap_server_unregister() API for LE L2CAP.
// Once registered, the server stays in Zephyr's internal list until hard reset.
static mp_bluetooth_zephyr_l2cap_server_t mp_bluetooth_zephyr_l2cap_static_server;
static bool mp_bluetooth_zephyr_l2cap_server_registered = false;

#endif // MICROPY_PY_BLUETOOTH_ENABLE_L2CAP_CHANNELS

typedef struct _mp_bluetooth_zephyr_root_pointers_t {
    // list of objects to be tracked by the gc
    mp_obj_t objs_list;

    // Characteristic (and descriptor) value storage.
    mp_gatts_db_t gatts_db;

    // Service definitions.
    size_t n_services;
    struct bt_gatt_service *services[MP_BLUETOOTH_ZEPHYR_MAX_SERVICES];

    // active connections
    mp_bt_zephyr_conn_t *connections;

    #if MICROPY_PY_BLUETOOTH_ENABLE_GATT_CLIENT
    // GATT client discovery state
    struct bt_gatt_discover_params gattc_discover_params;
    uint16_t gattc_discover_conn_handle;
    uint16_t gattc_discover_start_handle;
    uint16_t gattc_discover_end_handle;
    uint16_t gattc_discover_char_value_handle;  // Characteristic value handle for current descriptor discovery

    // Pending characteristic (for end_handle calculation - NimBLE pattern)
    struct {
        uint16_t value_handle;
        uint16_t def_handle;
        uint8_t properties;
        mp_obj_bluetooth_uuid_t uuid;
        bool pending;
    } gattc_pending_char;

    // GATT client read state
    struct bt_gatt_read_params gattc_read_params;
    uint16_t gattc_read_conn_handle;
    uint16_t gattc_read_value_handle;
    bool gattc_read_data_received; // Track if data callback was called for current read

    // GATT client write state
    struct bt_gatt_write_params gattc_write_params;
    uint16_t gattc_write_conn_handle;
    uint16_t gattc_write_value_handle;

    // MTU exchange state
    struct bt_gatt_exchange_params gattc_mtu_params;
    uint16_t gattc_mtu_conn_handle;

    // GATT client subscription state (for NOTIFY/INDICATE via explicit CCCD write)
    struct bt_gatt_subscribe_params gattc_subscribe_params;
    uint16_t gattc_subscribe_conn_handle;
    uint16_t gattc_subscribe_value_handle;
    uint16_t gattc_subscribe_ccc_handle;
    bool gattc_subscribe_active; // Track if subscription callback is registered
    bool gattc_subscribe_changing; // Track if we're intentionally switching subscription types
    bool gattc_unsubscribing; // Track if we're explicitly unsubscribing via CCCD write
    bool gattc_subscribe_pending; // Track if bt_gatt_subscribe was explicitly called

    // Auto-subscriptions for notification delivery without explicit CCCD write.
    // Zephyr's architecture requires subscriptions to be registered for notification
    // callbacks to fire, unlike NimBLE which delivers all notifications unconditionally.
    // These are registered during characteristic discovery for any characteristics
    // with notify/indicate properties.
    #ifndef GATTC_AUTO_SUBSCRIBE_MAX
    #define GATTC_AUTO_SUBSCRIBE_MAX 16  // Max simultaneous auto-subscribed handles
    #endif
    struct {
        struct bt_gatt_subscribe_params params;
        uint16_t conn_handle;
        bool in_use;
    } gattc_auto_subscriptions[GATTC_AUTO_SUBSCRIBE_MAX];
    #endif // MICROPY_PY_BLUETOOTH_ENABLE_GATT_CLIENT

    // Pairing/bonding state (Phase 1: Basic pairing without persistent storage)
    uint16_t auth_conn_handle;    // Connection undergoing authentication
    uint8_t auth_action;          // Pending passkey action type
    uint32_t auth_passkey;        // Passkey for display/comparison

    // Pairing state tracking (for deferred encryption callback)
    // On different platforms, security_changed and pairing_complete can arrive
    // in either order. We collect both pieces of data and fire _IRQ_ENCRYPTION_UPDATE
    // only when both have arrived.
    bool pairing_in_progress;       // True from pairing_confirm until encryption update fired
    bool pending_security_update;   // True if security_changed happened during pairing
    bool pairing_complete_received; // True if pairing_complete happened during pairing
    bool pending_pairing_bonded;    // Bonded flag from pairing_complete
    uint16_t pending_sec_conn;      // Connection handle for pending update
    bool pending_sec_encrypted;     // Encryption state
    bool pending_sec_authenticated; // Authentication state
    uint8_t pending_sec_key_size;   // Key size

    #if MICROPY_PY_BLUETOOTH_ENABLE_L2CAP_CHANNELS
    // L2CAP Connection-Oriented Channels state
    mp_bluetooth_zephyr_l2cap_channel_t *l2cap_chan;   // Current channel (dynamic per-connection)
    // Note: L2CAP server is static (mp_bluetooth_zephyr_l2cap_static_server) because
    // Zephyr has no bt_l2cap_server_unregister() API for LE L2CAP.
    bool l2cap_listening;                               // Whether listening this session
    #endif // MICROPY_PY_BLUETOOTH_ENABLE_L2CAP_CHANNELS
} mp_bluetooth_zephyr_root_pointers_t;

static int mp_bluetooth_zephyr_ble_state;

// Set during BLE deinit Phase 3 to make k_sem_take(K_FOREVER) fail immediately.
// Prevents stale work handlers from blocking on dead-connection semaphores
// during the bt_disable → k_sem_take → work_process recursion chain.
// Does NOT affect poll_uart — HCI transport must remain operational for bt_disable.
volatile bool mp_bluetooth_zephyr_deiniting = false;

// Set AFTER bt_disable() returns to prevent CYW43 SPI reads on the post-HCI_RESET
// controller. SPI reads on a reset controller can hang indefinitely, freezing the
// entire Pico W (including USB). Checked by poll_uart() and run_task().
volatile bool mp_bluetooth_zephyr_shutting_down = false;

#ifndef __ZEPHYR__
// BLE initialization completion tracking (-1 = pending, 0 = success, >0 = error code)
static volatile int mp_bluetooth_zephyr_bt_enable_result = -1;

// Timeout for BLE initialization (milliseconds)
#define ZEPHYR_BLE_STARTUP_TIMEOUT 5000
#endif

// Track if Zephyr callbacks are registered (persists across bt_enable/bt_disable cycles)
static bool mp_bt_zephyr_callbacks_registered = false;

#if MICROPY_PY_BLUETOOTH_ENABLE_CENTRAL_MODE
static int mp_bluetooth_zephyr_gap_scan_state;
static struct k_timer mp_bluetooth_zephyr_gap_scan_timer;
static struct bt_le_scan_cb mp_bluetooth_zephyr_gap_scan_cb_struct;
#endif

static struct bt_data bt_ad_data[8];
static size_t bt_ad_len = 0;
static struct bt_data bt_sd_data[8];
static size_t bt_sd_len = 0;

// Pool of indication params - must persist until callback fires
// One indication per connection can be in flight at a time
typedef struct _mp_bt_zephyr_indicate_params_t {
    struct bt_gatt_indicate_params params;
    bool in_use;
} mp_bt_zephyr_indicate_params_t;

static mp_bt_zephyr_indicate_params_t mp_bt_zephyr_indicate_pool[CONFIG_BT_MAX_CONN];

#if MICROPY_PY_BLUETOOTH_ENABLE_L2CAP_CHANNELS
// L2CAP SDU pool for TX. Buffer size includes headroom for L2CAP/HCI headers.
// Uses Zephyr's BT_L2CAP_SDU_BUF_SIZE to account for all required headroom.
// Keep buffer count low to minimize RAM usage - credit flow control handles pacing.
#define L2CAP_SDU_BUF_SIZE BT_L2CAP_SDU_BUF_SIZE(CONFIG_BT_L2CAP_TX_MTU)
#define L2CAP_SDU_BUF_COUNT 5
NET_BUF_POOL_FIXED_DEFINE(l2cap_sdu_pool, L2CAP_SDU_BUF_COUNT, L2CAP_SDU_BUF_SIZE,
    CONFIG_BT_CONN_TX_USER_DATA_SIZE, NULL);

// Forward declarations for L2CAP callbacks and helpers
static void l2cap_connected_cb(struct bt_l2cap_chan *chan);
static void l2cap_disconnected_cb(struct bt_l2cap_chan *chan);
static int l2cap_recv_cb(struct bt_l2cap_chan *chan, struct net_buf *buf);
static void l2cap_sent_cb(struct bt_l2cap_chan *chan);
static void l2cap_status_cb(struct bt_l2cap_chan *chan, atomic_t *status);
static struct net_buf *l2cap_alloc_buf_cb(struct bt_l2cap_chan *chan);
static int l2cap_server_accept_cb(struct bt_conn *conn, struct bt_l2cap_server *server,
                                  struct bt_l2cap_chan **chan);
static void l2cap_destroy_channel(void);

// L2CAP channel operations
static const struct bt_l2cap_chan_ops l2cap_chan_ops = {
    .connected = l2cap_connected_cb,
    .disconnected = l2cap_disconnected_cb,
    .recv = l2cap_recv_cb,
    .sent = l2cap_sent_cb,
    .status = l2cap_status_cb,
    .alloc_buf = l2cap_alloc_buf_cb,
};
#endif // MICROPY_PY_BLUETOOTH_ENABLE_L2CAP_CHANNELS

static mp_bt_zephyr_conn_t *mp_bt_zephyr_next_conn;

static mp_bt_zephyr_conn_t *mp_bt_zephyr_find_connection(uint8_t conn_handle);
static void mp_bt_zephyr_insert_connection(mp_bt_zephyr_conn_t *connection);
static void mp_bt_zephyr_remove_connection(uint8_t conn_handle);
static void mp_bt_zephyr_connected(struct bt_conn *connected, uint8_t err);
static void mp_bt_zephyr_disconnected(struct bt_conn *disconn, uint8_t reason);
static void mp_bt_zephyr_security_changed(struct bt_conn *conn, bt_security_t level, enum bt_security_err err);
static struct bt_uuid *create_zephyr_uuid(const mp_obj_bluetooth_uuid_t *uuid);
static void gatt_db_add(const struct bt_gatt_attr *pattern, struct bt_gatt_attr *attr, size_t user_data_len);
static void add_service(const struct bt_uuid *u, struct bt_gatt_attr *attr);
static void add_characteristic(struct add_characteristic *ch, struct bt_gatt_attr *attr_chrc, struct bt_gatt_attr *attr_value);
static void add_ccc(struct bt_gatt_attr *attr, struct bt_gatt_attr *attr_desc);
static void add_cep(const struct bt_gatt_attr *attr_chrc, struct bt_gatt_attr *attr_desc);
static void add_descriptor(struct bt_gatt_attr *chrc, struct add_descriptor *d, struct bt_gatt_attr *attr_desc);
static void mp_bt_zephyr_gatt_indicate_done(struct bt_conn *conn, struct bt_gatt_indicate_params *params, uint8_t err);
static void mp_bt_zephyr_gatt_indicate_destroy(struct bt_gatt_indicate_params *params);
static struct bt_gatt_attr *mp_bt_zephyr_find_attr_by_handle(uint16_t value_handle);
static void mp_bt_zephyr_free_service(struct bt_gatt_service *service);

static struct bt_conn_cb mp_bt_zephyr_conn_callbacks = {
    .connected = mp_bt_zephyr_connected,
    .disconnected = mp_bt_zephyr_disconnected,
    .security_changed = mp_bt_zephyr_security_changed,
};

// Get a unique connection handle from a bt_conn pointer by searching the connection list
// Returns the 0-based index of the connection in the list (0, 1, 2, ...)
// Returns 0xFF if connection not found
static uint8_t mp_bt_zephyr_conn_to_handle(struct bt_conn *conn) {
    if (!conn || !MP_STATE_PORT(bluetooth_zephyr_root_pointers)) {
        return 0xFF;
    }

    uint8_t handle = 0;
    for (mp_bt_zephyr_conn_t *connection = MP_STATE_PORT(bluetooth_zephyr_root_pointers)->connections;
         connection != NULL; connection = connection->next, handle++) {
        if (connection->conn == conn) {
            return handle;
        }
    }
    return 0xFF;
}

static mp_bt_zephyr_conn_t *mp_bt_zephyr_find_connection(uint8_t conn_handle) {
    if (!MP_STATE_PORT(bluetooth_zephyr_root_pointers)) {
        return NULL;
    }

    uint8_t idx = 0;
    for (mp_bt_zephyr_conn_t *connection = MP_STATE_PORT(bluetooth_zephyr_root_pointers)->connections;
         connection != NULL; connection = connection->next, idx++) {
        if (idx == conn_handle) {
            return connection;
        }
    }
    return NULL;
}

static void mp_bt_zephyr_insert_connection(mp_bt_zephyr_conn_t *connection) {
    connection->next = MP_STATE_PORT(bluetooth_zephyr_root_pointers)->connections;
    MP_STATE_PORT(bluetooth_zephyr_root_pointers)->connections = connection;
}

static void mp_bt_zephyr_remove_connection(uint8_t conn_handle) {
    if (!MP_STATE_PORT(bluetooth_zephyr_root_pointers)) {
        return;
    }

    mp_bt_zephyr_conn_t *prev = NULL;
    uint8_t idx = 0;
    for (mp_bt_zephyr_conn_t *connection = MP_STATE_PORT(bluetooth_zephyr_root_pointers)->connections;
         connection != NULL; connection = connection->next, idx++) {
        if (idx == conn_handle) {
            // unlink this item and the gc will eventually collect it
            if (prev != NULL) {
                prev->next = connection->next;
            } else {
                // move the start pointer
                MP_STATE_PORT(bluetooth_zephyr_root_pointers)->connections = connection->next;
            }
            break;
        }
        prev = connection;
    }
}

// Forward declaration - defined below in the scan section.
static void reverse_addr_byte_order(uint8_t *addr_out, const bt_addr_le_t *addr_in);

static void mp_bt_zephyr_connected(struct bt_conn *conn, uint8_t err) {
    DEBUG_printf("mp_bt_zephyr_connected: conn=%p err=%u state=%d\n",
        conn, err, mp_bluetooth_zephyr_ble_state);

    // Safety check: only process if BLE is fully active and initialized
    if (mp_bluetooth_zephyr_ble_state != MP_BLUETOOTH_ZEPHYR_BLE_STATE_ACTIVE
        || MP_STATE_PORT(bluetooth_zephyr_root_pointers) == NULL) {
        DEBUG_printf("  IGNORED - BLE not active (state=%d)\n",
                     mp_bluetooth_zephyr_ble_state);
        return;
    }

    #if defined(CONFIG_BT_BONDABLE_PER_CONNECTION) && CONFIG_BT_BONDABLE_PER_CONNECTION
    // Set per-connection bondable to match the current global setting
    bt_conn_set_bondable(conn, mp_bt_zephyr_bonding);
    #endif

    struct bt_conn_info info;
    bt_conn_get_info(conn, &info);

    // Determine correct IRQ event based on connection role:
    // - BT_HCI_ROLE_CENTRAL (0x00): Local initiated connection → PERIPHERAL_CONNECT/DISCONNECT
    // - BT_HCI_ROLE_PERIPHERAL (0x01): Remote initiated connection → CENTRAL_CONNECT/DISCONNECT
    uint16_t connect_event = (info.role == BT_HCI_ROLE_CENTRAL)
        ? MP_BLUETOOTH_IRQ_PERIPHERAL_CONNECT
        : MP_BLUETOOTH_IRQ_CENTRAL_CONNECT;
    uint16_t disconnect_event = (info.role == BT_HCI_ROLE_CENTRAL)
        ? MP_BLUETOOTH_IRQ_PERIPHERAL_DISCONNECT
        : MP_BLUETOOTH_IRQ_CENTRAL_DISCONNECT;

    if (err) {
        uint8_t addr[6] = {0};
        DEBUG_printf("Connection failed (err %u role %d)\n", err, info.role);
        // For outgoing connections, clean up stored conn reference.
        // Defensive NULL check: mp_bt_zephyr_next_conn could be NULL if
        // advertising was stopped or another callback path cleared it.
        if (mp_bt_zephyr_next_conn != NULL && mp_bt_zephyr_next_conn->conn != NULL) {
            DEBUG_printf("  Unref'ing failed outgoing connection %p\n", mp_bt_zephyr_next_conn->conn);
            bt_conn_unref(mp_bt_zephyr_next_conn->conn);
            mp_bt_zephyr_next_conn->conn = NULL;
        }
        // Don't free mp_bt_zephyr_next_conn here - it's registered with GC list
        // Reset pointer so next connection allocates fresh structure
        mp_bt_zephyr_next_conn = NULL;
        // Use 0xFF for failed connections - no valid handle exists
        uint8_t reversed_addr[6];
        reverse_addr_byte_order(reversed_addr, info.le.dst);
        mp_bluetooth_gap_on_connected_disconnected(disconnect_event, 0xFF, 0xff, reversed_addr);
    } else {
        DEBUG_printf("Connected with id %d role %d\n", info.id, info.role);
        // Take a reference to the connection for storage.
        // The callback's 'conn' parameter is a borrowed reference from Zephyr.
        //
        // Cases where mp_bt_zephyr_next_conn->conn is NULL:
        // 1. Incoming connections (peripheral role) - always NULL
        // 2. Outgoing connections with synchronous HCI (STM32WB) - callback fires
        //    DURING bt_conn_le_create before gap_connect can store the ref
        //
        // Cases where mp_bt_zephyr_next_conn->conn is set:
        // 1. Outgoing connections with async HCI (RP2 FreeRTOS) - gap_connect
        //    stored the ref from bt_conn_le_create before callback fires
        if (mp_bt_zephyr_next_conn->conn == NULL) {
            // Need our own reference - callback param is borrowed
            mp_bt_zephyr_next_conn->conn = bt_conn_ref(conn);
            DEBUG_printf("  Stored NEW connection ref %p\n", mp_bt_zephyr_next_conn->conn);
        } else {
            // Already have ref from bt_conn_le_create(), use it
            DEBUG_printf("  Using EXISTING connection ref %p\n", mp_bt_zephyr_next_conn->conn);
        }
        // Insert connection into tracking list BEFORE firing Python callback.
        // This ensures BLE operations (e.g., GATT discovery) can be performed
        // from within the IRQ handler - they need mp_bt_zephyr_find_connection()
        // to succeed.
        mp_bt_zephyr_insert_connection(mp_bt_zephyr_next_conn);
        // Get the actual connection handle from list position (not info.id which
        // is the Zephyr identity ID, always 0 with CONFIG_BT_ID_MAX=1).
        uint16_t conn_handle = mp_bt_zephyr_conn_to_handle(mp_bt_zephyr_next_conn->conn);
        // Reset pointer so next connection allocates fresh structure
        mp_bt_zephyr_next_conn = NULL;
        debug_check_uuid("before_connect_cb");
        uint8_t addr[6];
        reverse_addr_byte_order(addr, info.le.dst);
        mp_bluetooth_gap_on_connected_disconnected(connect_event, conn_handle, info.le.dst->type, addr);
        debug_check_uuid("after_connect_cb");
    }
}

static void mp_bt_zephyr_disconnected(struct bt_conn *conn, uint8_t reason) {
    DEBUG_printf("mp_bt_zephyr_disconnected: conn=%p reason=%u state=%d\n",
        conn, reason, mp_bluetooth_zephyr_ble_state);

    // Safety check: only process if BLE is fully active and initialized
    // Ignore callbacks during deinit (SUSPENDED state) to prevent double-unref race
    if (mp_bluetooth_zephyr_ble_state != MP_BLUETOOTH_ZEPHYR_BLE_STATE_ACTIVE
        || MP_STATE_PORT(bluetooth_zephyr_root_pointers) == NULL) {
        DEBUG_printf("Disconnected callback ignored - BLE not active (state=%d)\n",
                     mp_bluetooth_zephyr_ble_state);
        return;
    }

    struct bt_conn_info info;
    bt_conn_get_info(conn, &info);

    // Get actual connection handle from list position (not info.id which is
    // Zephyr identity ID). Must do this BEFORE removing from list.
    uint16_t conn_handle = mp_bt_zephyr_conn_to_handle(conn);

    // Determine correct IRQ event based on connection role:
    // - BT_HCI_ROLE_CENTRAL (0x00): Local initiated connection → PERIPHERAL_DISCONNECT
    // - BT_HCI_ROLE_PERIPHERAL (0x01): Remote initiated connection → CENTRAL_DISCONNECT
    uint16_t disconnect_event = (info.role == BT_HCI_ROLE_CENTRAL)
        ? MP_BLUETOOTH_IRQ_PERIPHERAL_DISCONNECT
        : MP_BLUETOOTH_IRQ_CENTRAL_DISCONNECT;

    DEBUG_printf("Disconnected (handle %d reason %u role %d)\n", conn_handle, reason, info.role);

    // Find our stored connection and unref it
    // Note: 'conn' parameter is a borrowed reference from Zephyr callback - don't unref it
    // We only unref the reference we explicitly took in mp_bt_zephyr_connected()
    mp_bt_zephyr_conn_t *stored = mp_bt_zephyr_find_connection(conn_handle);
    if (stored && stored->conn) {
        DEBUG_printf("  Unref'ing stored connection %p\n", stored->conn);
        bt_conn_unref(stored->conn);
        stored->conn = NULL;
    }

    mp_bluetooth_zephyr_root_pointers_t *rp = MP_STATE_PORT(bluetooth_zephyr_root_pointers);

    // Reset subscription state if this was the subscribed connection
    #if MICROPY_PY_BLUETOOTH_ENABLE_GATT_CLIENT
    if (rp && rp->gattc_subscribe_conn_handle == conn_handle) {
        rp->gattc_subscribe_active = false;
        rp->gattc_subscribe_changing = false;
        rp->gattc_unsubscribing = false;
        rp->gattc_subscribe_pending = false;
        rp->gattc_subscribe_conn_handle = 0;
        rp->gattc_subscribe_ccc_handle = 0;
        rp->gattc_subscribe_value_handle = 0;
    }

    // Clear auto-subscriptions for this connection
    gattc_clear_auto_subscriptions(conn_handle);
    #endif // MICROPY_PY_BLUETOOTH_ENABLE_GATT_CLIENT

    // Clear pairing state on disconnect
    if (rp) {
        rp->pairing_in_progress = false;
        rp->pending_security_update = false;
        rp->pairing_complete_received = false;
    }

    // Fire Python callback BEFORE removing from list, so cleanup operations
    // in the callback can still access the connection if needed.
    uint8_t addr[6];
    reverse_addr_byte_order(addr, info.le.dst);
    mp_bluetooth_gap_on_connected_disconnected(disconnect_event, conn_handle, info.le.dst->type, addr);
    mp_bt_zephyr_remove_connection(conn_handle);
}

static void mp_bt_zephyr_security_changed(struct bt_conn *conn, bt_security_t level, enum bt_security_err err) {
    // Decode security level and error for debugging
    const char *level_str;
    switch (level) {
        case BT_SECURITY_L0: level_str = "L0 (no sec)"; break;
        case BT_SECURITY_L1: level_str = "L1 (no auth no enc)"; break;
        case BT_SECURITY_L2: level_str = "L2 (enc, no auth)"; break;
        case BT_SECURITY_L3: level_str = "L3 (enc + auth)"; break;
        case BT_SECURITY_L4: level_str = "L4 (SC + auth)"; break;
        default:             level_str = "UNKNOWN"; break;
    }

    const char *err_str;
    switch (err) {
        case BT_SECURITY_ERR_SUCCESS:         err_str = "SUCCESS"; break;
        case BT_SECURITY_ERR_AUTH_FAIL:       err_str = "AUTH_FAIL"; break;
        case BT_SECURITY_ERR_PIN_OR_KEY_MISSING: err_str = "PIN_OR_KEY_MISSING"; break;
        case BT_SECURITY_ERR_OOB_NOT_AVAILABLE: err_str = "OOB_NOT_AVAILABLE"; break;
        case BT_SECURITY_ERR_AUTH_REQUIREMENT: err_str = "AUTH_REQUIREMENT"; break;
        case BT_SECURITY_ERR_PAIR_NOT_SUPPORTED: err_str = "PAIR_NOT_SUPPORTED"; break;
        case BT_SECURITY_ERR_PAIR_NOT_ALLOWED: err_str = "PAIR_NOT_ALLOWED"; break;
        case BT_SECURITY_ERR_INVALID_PARAM:   err_str = "INVALID_PARAM"; break;
        case BT_SECURITY_ERR_UNSPECIFIED:     err_str = "UNSPECIFIED"; break;
        default:                               err_str = "UNKNOWN"; break;
    }

    DEBUG_printf(">>> mp_bt_zephyr_security_changed: level=%d (%s) err=%d (%s)\n",
                 level, level_str, err, err_str);

    // Safety check: only process if BLE is fully active
    if (mp_bluetooth_zephyr_ble_state != MP_BLUETOOTH_ZEPHYR_BLE_STATE_ACTIVE
        || MP_STATE_PORT(bluetooth_zephyr_root_pointers) == NULL) {
        DEBUG_printf("Security changed callback ignored - BLE not active\n");
        return;
    }

    // Get connection handle
    uint16_t conn_handle = mp_bt_zephyr_conn_to_handle(conn);
    if (conn_handle == 0xFF) {
        DEBUG_printf("Security changed: connection not found\n");
        return;
    }

    // Get full security state
    struct bt_conn_info info;
    if (bt_conn_get_info(conn, &info) != 0) {
        DEBUG_printf("Security changed: bt_conn_get_info failed\n");
        return;
    }

    DEBUG_printf("  security.level=%d flags=0x%02x enc_key_size=%d\n",
                 info.security.level, info.security.flags, info.security.enc_key_size);

    // Determine encryption and authentication status from security level
    // BT_SECURITY_L1 = No security (no encryption, no authentication)
    // BT_SECURITY_L2 = Encryption only (unauthenticated pairing)
    // BT_SECURITY_L3 = Authenticated pairing (MITM protection)
    // BT_SECURITY_L4 = Authenticated LE Secure Connections (LESC)
    bool encrypted = (info.security.level >= BT_SECURITY_L2);
    bool authenticated = (info.security.level >= BT_SECURITY_L3);
    uint8_t key_size = info.security.enc_key_size;

    mp_bluetooth_zephyr_root_pointers_t *rp = MP_STATE_PORT(bluetooth_zephyr_root_pointers);

    // During pairing, security_changed and pairing_complete can arrive in either order
    // depending on the platform (HAL vs native Zephyr). We store the security info and
    // fire _IRQ_ENCRYPTION_UPDATE only when both have arrived.
    if (rp && rp->pairing_in_progress) {
        DEBUG_printf("Security changed: pairing in progress, storing security info\n");
        rp->pending_security_update = true;
        rp->pending_sec_conn = conn_handle;
        rp->pending_sec_encrypted = encrypted;
        rp->pending_sec_authenticated = authenticated;
        rp->pending_sec_key_size = key_size;

        // Check if pairing_complete already arrived (it fires first on HAL builds)
        if (rp->pairing_complete_received) {
            DEBUG_printf("Both security_changed and pairing_complete received, firing callback\n");
            rp->pairing_in_progress = false;
            rp->pending_security_update = false;
            rp->pairing_complete_received = false;
            mp_bluetooth_gatts_on_encryption_update(conn_handle, encrypted, authenticated,
                                                    rp->pending_pairing_bonded, key_size);
        }
        return;
    }

    // No pairing in progress - this is re-encryption with existing keys
    // Fire callback immediately with bonded=false (no new bond created)
    bool bonded = false;
    DEBUG_printf("Firing _IRQ_ENCRYPTION_UPDATE: encrypted=%d authenticated=%d bonded=%d key_size=%d\n",
                 encrypted, authenticated, bonded, key_size);
    mp_bluetooth_gatts_on_encryption_update(conn_handle, encrypted, authenticated, bonded, key_size);
}

static int bt_err_to_errno(int err) {
    // Zephyr uses errno codes directly, but they are negative.
    return -err;
}

// modbluetooth (and the layers above it) work in BE for addresses, Zephyr works in LE.
static void reverse_addr_byte_order(uint8_t *addr_out, const bt_addr_le_t *addr_in) {
    for (int i = 0; i < 6; ++i) {
        addr_out[i] = addr_in->a.val[5 - i];
    }
}

#ifndef __ZEPHYR__
// Callback for bt_enable() completion (HAL build only — native Zephyr uses synchronous bt_enable)
static void mp_bluetooth_zephyr_bt_ready_cb(int err) {
    DEBUG_printf("bt_ready_cb: err=%d\n", err);
    mp_bluetooth_zephyr_bt_enable_result = err;
}
#endif

#if MICROPY_PY_BLUETOOTH_ENABLE_CENTRAL_MODE

void gap_scan_cb_recv(const struct bt_le_scan_recv_info *info, struct net_buf_simple *buf) {
    DEBUG_printf("gap_scan_cb_recv: adv_type=%d\n", info->adv_type);

    if (!mp_bluetooth_is_active()) {
        DEBUG_printf("  --> BLE not active, skipping\n");
        return;
    }

    if (mp_bluetooth_zephyr_gap_scan_state != MP_BLUETOOTH_ZEPHYR_GAP_SCAN_STATE_ACTIVE) {
        DEBUG_printf("  --> scan state not active (%d), skipping\n", mp_bluetooth_zephyr_gap_scan_state);
        return;
    }

    uint8_t addr[6];
    reverse_addr_byte_order(addr, info->addr);
    mp_bluetooth_gap_on_scan_result(info->addr->type, addr, info->adv_type, info->rssi, buf->data, buf->len);
    DEBUG_printf("  --> delivered to Python IRQ handler\n");
}

static mp_obj_t gap_scan_stop(mp_obj_t unused) {
    (void)unused;
    mp_bluetooth_gap_scan_stop();
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(gap_scan_stop_obj, gap_scan_stop);

void gap_scan_cb_timeout(struct k_timer *timer_id) {
    DEBUG_printf("gap_scan_cb_timeout\n");
    // Cannot call bt_le_scan_stop from a timer callback because this callback may be
    // preempting the BT stack.  So schedule it to be called from the main thread.
    while (!mp_sched_schedule(MP_OBJ_FROM_PTR(&gap_scan_stop_obj), mp_const_none)) {
        #ifdef __ZEPHYR__
        // Native Zephyr: yield to let main thread drain the scheduler queue.
        k_yield();
        #else
        // HAL build: process work queue to make space.
        mp_bluetooth_zephyr_poll();
        #endif
    }
    // Indicate scanning has stopped so that no more scan result events are generated
    // (they may still come in until bt_le_scan_stop is called by gap_scan_stop).
    mp_bluetooth_zephyr_gap_scan_state = MP_BLUETOOTH_ZEPHYR_GAP_SCAN_STATE_DEACTIVATING;
}
#endif

// Forward declarations for authentication callbacks (defined later in file)
extern struct bt_conn_auth_cb mp_bt_zephyr_auth_callbacks;
extern struct bt_conn_auth_info_cb mp_bt_zephyr_auth_info_callbacks;

// Forward declaration for GATT callbacks (handles remote-initiated MTU exchange)
extern struct bt_gatt_cb mp_bt_zephyr_gatt_callbacks;

// Helper to get conn_handle from bt_conn for auth callbacks
// Returns 0xFF if BLE not active or connection not found
static inline uint8_t mp_bt_zephyr_auth_get_conn_handle(struct bt_conn *conn) {
    if (!mp_bluetooth_is_active()) {
        return 0xFF;
    }
    return mp_bt_zephyr_conn_to_handle(conn);
}

int mp_bluetooth_init(void) {
    DEBUG_printf("mp_bluetooth_init\n");

    // Clean up if necessary.
    mp_bluetooth_deinit();

    // Allocate memory for state.
    MP_STATE_PORT(bluetooth_zephyr_root_pointers) = m_new0(mp_bluetooth_zephyr_root_pointers_t, 1);
    mp_bluetooth_gatts_db_create(&MP_STATE_PORT(bluetooth_zephyr_root_pointers)->gatts_db);

    MP_STATE_PORT(bluetooth_zephyr_root_pointers)->connections = NULL;
    mp_bt_zephyr_next_conn = NULL;

    MP_STATE_PORT(bluetooth_zephyr_root_pointers)->objs_list = mp_obj_new_list(0, NULL);

    #if MICROPY_PY_BLUETOOTH_ENABLE_CENTRAL_MODE
    // Zero the scan callback structure to ensure sys_snode_t is initialized
    memset(&mp_bluetooth_zephyr_gap_scan_cb_struct, 0, sizeof(mp_bluetooth_zephyr_gap_scan_cb_struct));
    mp_bluetooth_zephyr_gap_scan_state = MP_BLUETOOTH_ZEPHYR_GAP_SCAN_STATE_INACTIVE;
    k_timer_init(&mp_bluetooth_zephyr_gap_scan_timer, gap_scan_cb_timeout, NULL);
    mp_bluetooth_zephyr_gap_scan_cb_struct.recv = gap_scan_cb_recv;
    mp_bluetooth_zephyr_gap_scan_cb_struct.timeout = NULL; // currently not implemented in Zephyr
    #endif

    // Only initialize the BLE stack if not already ACTIVE
    int current_state = mp_bluetooth_zephyr_ble_state;
    if (current_state != MP_BLUETOOTH_ZEPHYR_BLE_STATE_ACTIVE) {
        // First-time initialization: port resources and controller
        // Only do this when coming from OFF state, not when reinitializing from SUSPENDED
        if (current_state == MP_BLUETOOTH_ZEPHYR_BLE_STATE_OFF) {
            // Initialize port-specific resources (soft timers, sched nodes, etc.)
            #if MICROPY_PY_NETWORK_CYW43 || MICROPY_PY_BLUETOOTH_USE_ZEPHYR_HCI || defined(STM32WB) || defined(__ZEPHYR__)
            mp_bluetooth_zephyr_port_init();
            #endif

            // Initialize HCI controller (CYW43 BT via WEAK override from pico-sdk)
            // This must be called before bt_enable()
            #if MICROPY_PY_BLUETOOTH_USE_ZEPHYR_HCI
            extern int mp_bluetooth_hci_controller_init(void);
            int ctrl_ret = mp_bluetooth_hci_controller_init();
            if (ctrl_ret != 0) {
                DEBUG_printf("Controller init failed with code %d\n", ctrl_ret);
                return ctrl_ret;
            }
            #endif

            // Register Zephyr callbacks only once per session
            // Callbacks persist across bt_disable()/bt_enable() cycles, so we track
            // registration separately from BLE state to avoid duplicate registration
            if (!mp_bt_zephyr_callbacks_registered) {
                bt_conn_cb_register(&mp_bt_zephyr_conn_callbacks);

                #if MICROPY_PY_BLUETOOTH_ENABLE_CENTRAL_MODE
                bt_le_scan_cb_register(&mp_bluetooth_zephyr_gap_scan_cb_struct);
                #endif

                #if MICROPY_PY_BLUETOOTH_ENABLE_PAIRING_BONDING
                // Configure default IO capability (Just Works / NO_INPUT_NO_OUTPUT)
                // This must be called before registering auth callbacks
                mp_bluetooth_set_io_capability(0);

                // Register authentication callbacks for pairing/bonding
                bt_conn_auth_cb_register(&mp_bt_zephyr_auth_callbacks);
                bt_conn_auth_info_cb_register(&mp_bt_zephyr_auth_info_callbacks);
                #endif

                // Register GATT callbacks for MTU updates (handles remote-initiated MTU exchange)
                bt_gatt_cb_register(&mp_bt_zephyr_gatt_callbacks);

                mp_bt_zephyr_callbacks_registered = true;
                DEBUG_printf("Zephyr callbacks registered\n");
            }
        }

        // Initialize Zephyr BLE host stack

        #ifdef __ZEPHYR__
        // Native Zephyr: synchronous bt_enable (real kernel threads handle init)
        int ret = bt_enable(NULL);
        if (ret == -EALREADY) {
            // Stack already enabled from previous init (SUSPENDED state)
            goto init_complete;
        }
        if (ret) {
            return bt_err_to_errno(ret);
        }
        #else
        // HAL build: async bt_enable with manual init loop
        // Note: After bt_disable(), we can call bt_enable() again to reinitialize

        // Reset completion flag
        mp_bluetooth_zephyr_bt_enable_result = -1;

        // NOTE: HCI RX task is started AFTER bt_enable() completes
        // During bt_enable(), polling mode is used for HCI reception
        // After bt_enable(), HCI RX task takes over for better performance

        // Start the dedicated BLE work queue thread (FreeRTOS builds only)
        // This must also be started before bt_enable() to process work items
        mp_bluetooth_zephyr_work_thread_start();

        // Reset net_buf pool state before BLE initialization.
        // After a soft reset, pools retain stale runtime state (free list,
        // uninit_count) from the previous session. This causes crashes when
        // bt_enable() tries to allocate buffers from corrupted pools.
        mp_net_buf_pool_state_reset();

        // Clear any stale bond keys from previous session. If bt_disable()
        // failed (e.g. CYW43 SPI hang), bt_keys_reset() in bt_disable was
        // never reached and stale keys persist. These cause the host to attempt
        // re-encryption with dead keys on the next connection, breaking Z2Z.
        #if defined(CONFIG_BT_SMP)
        extern void bt_keys_reset(void);
        bt_keys_reset();
        #endif

        // Clear stale GATT client subscriptions from previous session. After
        // soft reset, bt_gatt_subscribe_params on the GC heap are freed but
        // Zephyr's static subscriptions[] array still references them. On the
        // next disconnect, remove_subscriptions() calls params->notify()
        // through the stale pointer → HardFault. This is a raw memset that
        // doesn't call any callbacks (unlike bt_gatt_clear_subscriptions).
        #if defined(CONFIG_BT_GATT_CLIENT)
        extern void bt_gatt_reset_subscriptions(void);
        bt_gatt_reset_subscriptions();
        #endif

        // Enter init phase - work will be processed synchronously in this loop
        extern void mp_bluetooth_zephyr_init_phase_enter(void);
        mp_bluetooth_zephyr_init_phase_enter();

        // Call bt_enable() with ready callback
        int ret = bt_enable(mp_bluetooth_zephyr_bt_ready_cb);

        // Handle -EALREADY: stack is already enabled (reactivation from SUSPENDED)
        // In this case, skip the init loop and just restart our tasks
        if (ret == -EALREADY) {
            DEBUG_printf("BLE stack already enabled (reactivation)\n");
            mp_bluetooth_zephyr_bt_enable_result = 0;  // Mark as success
            // Exit init phase since we're not going through the loop
            extern void mp_bluetooth_zephyr_init_phase_exit(void);
            mp_bluetooth_zephyr_init_phase_exit();
            goto init_complete;
        }

        if (ret) {
            return bt_err_to_errno(ret);
        }

        // Wait synchronously until initialization completes (same pattern as NimBLE)
        // Get init work once and execute it in main loop context, allowing it to yield
        DEBUG_printf("Waiting for BLE initialization to complete...\n");
        DEBUG_SEQ_printf("Starting wait loop\n");
        mp_uint_t timeout_start_ticks_ms = mp_hal_ticks_ms();

        // Declare init work pointer (dequeued once, executed until complete)
        extern struct k_work *mp_bluetooth_zephyr_init_work_get(void);
        struct k_work *init_work = NULL;

        while (mp_bluetooth_zephyr_bt_enable_result < 0) {  // -1 = pending
            uint32_t elapsed = mp_hal_ticks_ms() - timeout_start_ticks_ms;
            if (elapsed > ZEPHYR_BLE_STARTUP_TIMEOUT) {
                DEBUG_printf("BLE initialization timeout after %u ms\n", elapsed);
                DEBUG_SEQ_printf("Timeout reached\n");
                break;
            }

            // Get and execute init work once (bt_dev.init work item)
            // The handler (bt_init) will block internally in k_sem_take() loops,
            // but those loops yield via mp_event_wait_ms(), allowing scheduler to run
            if (init_work == NULL) {
                DEBUG_SEQ_printf("Attempting to get init work\n");
                init_work = mp_bluetooth_zephyr_init_work_get();
                if (init_work != NULL && init_work->handler != NULL) {
                    DEBUG_printf("init work=%p handler=%p\n", init_work, init_work->handler);
                    DEBUG_SEQ_printf("Executing init work handler\n");
                    DEBUG_ENTER("init_work->handler");
                    // Set work queue context so k_current_get() returns &k_sys_work_q.thread
                    // This enables Zephyr's synchronous HCI command path
                    extern void mp_bluetooth_zephyr_set_sys_work_q_context(bool in_context);
                    mp_bluetooth_zephyr_set_sys_work_q_context(true);
                    init_work->handler(init_work);
                    mp_bluetooth_zephyr_set_sys_work_q_context(false);
                    DEBUG_EXIT("init_work->handler");
                    DEBUG_printf("init handler done, result=%d\n",
                        mp_bluetooth_zephyr_bt_enable_result);
                    // Handler has completed (bt_init ran to completion)
                    // bt_ready_cb should have been called and set result flag
                } else {
                    DEBUG_printf("no init work (work=%p)\n", init_work);
                    DEBUG_SEQ_printf("No init work found\n");
                }
            }

            // Yield and run scheduler to process HCI responses
            // This delivers HCI responses that signal semaphores in init work
            DEBUG_SEQ_printf("Wait loop: elapsed=%u ms, result=%d\n", elapsed, mp_bluetooth_zephyr_bt_enable_result);
            mp_event_wait_ms(1);
        }

        // Exit init phase - work thread can now process work
        extern void mp_bluetooth_zephyr_init_phase_exit(void);
        mp_bluetooth_zephyr_init_phase_exit();
        #endif // __ZEPHYR__ (closes #ifdef/#else for init sequence)

        #ifndef __ZEPHYR__
        // Check result (HAL build only — native Zephyr uses synchronous bt_enable)
        if (mp_bluetooth_zephyr_bt_enable_result != 0) {
            int err = mp_bluetooth_zephyr_bt_enable_result;
            mp_bluetooth_deinit();
            if (err < 0) {
                // Timeout
                DEBUG_printf("BLE initialization failed: timeout\n");
                return MP_ETIMEDOUT;
            } else {
                // Zephyr error code
                DEBUG_printf("BLE initialization failed: error=%d\n", err);
                return bt_err_to_errno(err);
            }
        }
        #endif // !__ZEPHYR__

    init_complete:
        DEBUG_printf("BLE initialization successful!\n");

        #if defined(CONFIG_SETTINGS)
        // Load settings from flash (required for BT_SETTINGS to restore keys).
        // Must be called after bt_enable() and before any BLE operations.
        settings_load();
        #endif

        #if MICROPY_PY_BLUETOOTH_ENABLE_PAIRING_BONDING && !defined(__ZEPHYR__)
        // Load stored bond keys from Python secret callbacks into Zephyr's key_pool.
        // Native Zephyr port uses settings_load() above instead.
        mp_bluetooth_zephyr_load_keys();
        #endif

        #ifndef __ZEPHYR__
        // Start HCI RX task for continuous HCI polling in background
        // The task is stopped first in mp_bluetooth_deinit() to prevent race conditions
        #if MICROPY_BLUETOOTH_ZEPHYR_USE_FREERTOS
        mp_bluetooth_zephyr_hci_rx_task_start();
        DEBUG_printf("HCI RX task started\n");
        #endif
        #endif // !__ZEPHYR__

    } else {
        DEBUG_printf("BLE already ACTIVE (state=%d)\n", mp_bluetooth_zephyr_ble_state);
    }

    mp_bluetooth_zephyr_ble_state = MP_BLUETOOTH_ZEPHYR_BLE_STATE_ACTIVE;

    // Start the HCI polling cycle by triggering the first poll
    // This must be done after state is ACTIVE so mp_bluetooth_hci_poll() will run
    mp_bluetooth_hci_poll_now();

    DEBUG_printf("mp_bluetooth_init: ready\n");

    return 0;
}

#ifdef __ZEPHYR__
// Callback for bt_conn_foreach to disconnect and count LE connections.
static void mp_bt_zephyr_disconnect_count_cb(struct bt_conn *conn, void *data) {
    int *count = (int *)data;
    (*count)++;
    struct bt_conn_info info;
    int info_err = bt_conn_get_info(conn, &info);
    if (info_err == 0) {
        DEBUG_printf("mp_bluetooth_deinit: conn %p state=%d role=%d\n", conn, info.state, info.role);
    } else {
        DEBUG_printf("mp_bluetooth_deinit: conn %p (get_info failed: %d)\n", conn, info_err);
    }
    int err = bt_conn_disconnect(conn, BT_HCI_ERR_REMOTE_USER_TERM_CONN);
    DEBUG_printf("mp_bluetooth_deinit: bt_conn_disconnect returned %d\n", err);
}

// Callback for bt_conn_foreach to count remaining LE connections.
static void mp_bt_zephyr_count_conn_cb(struct bt_conn *conn, void *data) {
    int *count = (int *)data;
    (*count)++;
    struct bt_conn_info info;
    if (bt_conn_get_info(conn, &info) == 0) {
        DEBUG_printf("  still active: conn %p state=%d\n", conn, info.state);
    }
}

// Disconnect all LE connections and wait for them to complete.
// On native Zephyr the BT RX thread processes disconnect events independently
// so we just need to yield and check periodically.
static void mp_bt_zephyr_disconnect_all_wait(void) {
    int count = 0;
    bt_conn_foreach(BT_CONN_TYPE_LE, mp_bt_zephyr_disconnect_count_cb, &count);
    if (count == 0) {
        return;
    }
    DEBUG_printf("mp_bluetooth_deinit: waiting for %d connection(s) to disconnect\n", count);
    // Wait up to 1 second for disconnections to complete.
    // The BT RX thread runs at higher priority and will process events.
    for (int i = 0; i < 100; i++) {
        k_sleep(K_MSEC(10));
        count = 0;
        bt_conn_foreach(BT_CONN_TYPE_LE, mp_bt_zephyr_count_conn_cb, &count);
        if (count == 0) {
            DEBUG_printf("mp_bluetooth_deinit: all connections disconnected\n");
            return;
        }
    }
    DEBUG_printf("mp_bluetooth_deinit: %d connection(s) still active after timeout\n", count);
}
#endif

int mp_bluetooth_deinit(void) {
    DEBUG_printf("mp_bluetooth_deinit %d\n", mp_bluetooth_zephyr_ble_state);
    if (mp_bluetooth_zephyr_ble_state == MP_BLUETOOTH_ZEPHYR_BLE_STATE_OFF) {
        return 0;
    }

    #ifdef __ZEPHYR__
    // On native Zephyr, SUSPENDED means the Zephyr BT stack is still active but
    // MicroPython state from a previous session may be stale (GC heap reinitialized).
    // Only stop active BLE operations on the Zephyr stack; skip MicroPython heap
    // cleanup (services, L2CAP channels) since those pointers may be invalid.
    if (mp_bluetooth_zephyr_ble_state == MP_BLUETOOTH_ZEPHYR_BLE_STATE_SUSPENDED) {
        DEBUG_printf("mp_bluetooth_deinit: SUSPENDED, stopping BLE operations only\n");
        bt_le_adv_stop();
        #if MICROPY_PY_BLUETOOTH_ENABLE_CENTRAL_MODE
        bt_le_scan_stop();
        #endif
        // Disconnect any active connections and wait for completion.
        // Connection pool slots must be freed before next bt_le_adv_start.
        mp_bt_zephyr_disconnect_all_wait();
        return 0;
    }
    #endif

    // === PHASE 1: Stop HCI RX task FIRST ===
    // This MUST happen before bt_le_adv_stop/bt_le_scan_stop/bt_disable to prevent race:
    // - Those functions send HCI commands and wait for responses
    // - If HCI RX task is running, it queues responses as work items
    // - This can cause deadlock: work_drain can't keep up with new work
    // By stopping HCI RX task first, all HCI operations fall back to polling mode.
    mp_bluetooth_zephyr_hci_rx_task_stop();

    // Clean up pre-allocated connection object
    if (mp_bt_zephyr_next_conn != NULL) {
        DEBUG_printf("mp_bluetooth_deinit: cleaning up pre-allocated connection\n");
        mp_bt_zephyr_next_conn = NULL;
    }

    // === PHASE 2: Stop active BLE operations ===
    // These may fail during soft reset if stack is in bad state.
    // We ignore errors here to ensure cleanup continues.

    DEBUG_printf("mp_bluetooth_deinit: stopping advertising\n");
    int ret = bt_le_adv_stop();
    if (ret != 0 && ret != -EALREADY) {
        DEBUG_printf("mp_bluetooth_deinit: bt_le_adv_stop returned %d (ignored)\n", ret);
    }

    #if MICROPY_PY_BLUETOOTH_ENABLE_CENTRAL_MODE
    DEBUG_printf("mp_bluetooth_deinit: stopping scan\n");
    ret = bt_le_scan_stop();
    if (ret != 0 && ret != -EALREADY) {
        DEBUG_printf("mp_bluetooth_deinit: bt_le_scan_stop returned %d (ignored)\n", ret);
    }
    #endif

    #ifdef __ZEPHYR__
    // On native Zephyr (no bt_disable), explicitly disconnect all connections
    // and wait for the connection pool slots to be freed.
    mp_bt_zephyr_disconnect_all_wait();
    #endif

    #if MICROPY_PY_BLUETOOTH_ENABLE_L2CAP_CHANNELS
    // Disconnect active L2CAP channels and clean up.
    // Clear root pointer FIRST to prevent callbacks from double-freeing.
    mp_bluetooth_zephyr_root_pointers_t *rp = MP_STATE_PORT(bluetooth_zephyr_root_pointers);
    if (rp && rp->l2cap_chan) {
        DEBUG_printf("mp_bluetooth_deinit: disconnecting L2CAP channel\n");
        mp_bluetooth_zephyr_l2cap_channel_t *chan = rp->l2cap_chan;
        rp->l2cap_chan = NULL;  // Prevent callback from double-freeing
        if (chan->le_chan.chan.conn) {
            bt_l2cap_chan_disconnect(&chan->le_chan.chan);
        }
        // Cleanup inline (l2cap_destroy_channel checks rp->l2cap_chan which is now NULL)
        if (chan->rx_buf) {
            m_del(uint8_t, chan->rx_buf, L2CAP_RX_BUF_SIZE);
        }
        m_del(mp_bluetooth_zephyr_l2cap_channel_t, chan, 1);
    }
    if (rp) {
        // Note: Zephyr has no bt_l2cap_server_unregister() for LE L2CAP.
        // The static server structure persists across soft resets and we
        // track registration with mp_bluetooth_zephyr_l2cap_server_registered.
        // Just clear the session-level listening flag.
        rp->l2cap_listening = false;
    }
    #endif

    #if CONFIG_BT_GATT_DYNAMIC_DB
    for (size_t i = 0; i < MP_STATE_PORT(bluetooth_zephyr_root_pointers)->n_services; ++i) {
        struct bt_gatt_service *service = MP_STATE_PORT(bluetooth_zephyr_root_pointers)->services[i];
        if (service != NULL) {
            bt_gatt_service_unregister(service);
            mp_bt_zephyr_free_service(service);
            MP_STATE_PORT(bluetooth_zephyr_root_pointers)->services[i] = NULL;
        }
    }
    MP_STATE_PORT(bluetooth_zephyr_root_pointers)->n_services = 0;
    #endif

    #ifdef __ZEPHYR__
    // Native Zephyr: bt_disable not reliably available across Zephyr versions.
    // Set SUSPENDED so next init reuses the already-enabled stack.
    mp_bluetooth_zephyr_ble_state = MP_BLUETOOTH_ZEPHYR_BLE_STATE_SUSPENDED;
    #else
    // === PHASE 3: bt_disable() with polling mode ===
    // Signal deiniting to make k_sem_take(K_FOREVER) fail immediately. This
    // prevents stale work handlers (from bt_conn_cleanup_all inside bt_disable)
    // from blocking on dead-connection semaphores during the
    // bt_disable → k_sem_take → hci_uart_wfi → work_process chain.
    mp_bluetooth_zephyr_deiniting = true;

    // Block CYW43 SPI reads from non-wait-loop callers (run_task via soft timer).
    // poll_uart() allows reads when in_wait_loop=true (k_sem_take polling) so
    // bt_disable's HCI_RESET can complete. After bt_disable returns and the wait
    // loop exits, all SPI reads are blocked — the controller is in reset state.
    mp_bluetooth_zephyr_shutting_down = true;

    // Discard stale work items left over from the now-dead BLE connection.
    // bt_disable() will submit fresh tx_work for the HCI_Reset command.
    extern void mp_bluetooth_zephyr_work_clear_pending(void);
    mp_bluetooth_zephyr_work_clear_pending();

    DEBUG_printf("mp_bluetooth_deinit: calling bt_disable\n");
    ret = bt_disable();
    DEBUG_printf("mp_bluetooth_deinit: bt_disable returned %d\n", ret);

    // If bt_disable() failed (e.g. timeout), force-clear all state
    // so that bt_enable() can start fresh on next init.
    if (ret != 0) {
        DEBUG_printf("mp_bluetooth_deinit: bt_disable failed, force-clearing state\n");
        // Clear BT_DEV_ENABLE, BT_DEV_DISABLE, BT_DEV_READY flags
        // These are defined in hci_core.h as enum values 0, 1, 2
        atomic_clear_bit(bt_dev.flags, BT_DEV_ENABLE);
        atomic_clear_bit(bt_dev.flags, BT_DEV_DISABLE);
        atomic_clear_bit(bt_dev.flags, BT_DEV_READY);

        // On CYW43, also reset bt_loaded to force firmware re-download
        // The controller is in an unknown state if HCI_Reset didn't complete
        #if MICROPY_PY_NETWORK_CYW43
        extern cyw43_t cyw43_state;
        DEBUG_printf("mp_bluetooth_deinit: resetting cyw43_state.bt_loaded\n");
        cyw43_state.bt_loaded = false;
        #endif

        // Clear stale bond keys left behind when bt_disable() failed before
        // reaching bt_keys_reset(). Without this, the next bt_enable() starts
        // with stale keys which can cause spurious re-encryption attempts.
        extern void bt_keys_reset(void);
        bt_keys_reset();

        // Re-initialize the command credit semaphore so bt_enable can start fresh.
        // After a timeout, ncmd_sem may be depleted (0 credits) preventing new commands.
        DEBUG_printf("mp_bluetooth_deinit: reinitializing bt_dev.ncmd_sem\n");
        k_sem_init(&bt_dev.ncmd_sem, 1, 1);
    }

    // === PHASE 4: Drain remaining work ===
    // Now safe to drain - no new work will be added (HCI RX task is stopped)
    extern bool mp_bluetooth_zephyr_work_drain(void);
    mp_bluetooth_zephyr_work_drain();

    // Stop work thread
    mp_bluetooth_zephyr_work_thread_stop();

    // Reset work queue state to clear stale queue linkages
    extern void mp_bluetooth_zephyr_work_reset(void);
    mp_bluetooth_zephyr_work_reset();

    // Reset net_buf pool state to prevent corruption across soft resets.
    // Pools retain runtime state (free list, uninit_count, lock) from the
    // previous session which can cause crashes on next init.
    mp_net_buf_pool_state_reset();

    // Set state to OFF so next init does full re-initialization
    // (including controller init and callback registration)
    mp_bluetooth_zephyr_ble_state = MP_BLUETOOTH_ZEPHYR_BLE_STATE_OFF;
    #endif // __ZEPHYR__

    // Deinit port-specific resources (soft timers, GATT pool, etc.)
    mp_bluetooth_zephyr_port_deinit();

    MP_STATE_PORT(bluetooth_zephyr_root_pointers) = NULL;
    mp_bt_zephyr_next_conn = NULL;

    // Reset indication pool - all indications should be done by now
    memset(mp_bt_zephyr_indicate_pool, 0, sizeof(mp_bt_zephyr_indicate_pool));

    // Note: We intentionally do NOT reset mp_bt_zephyr_callbacks_registered here.
    // Zephyr callbacks persist across bt_disable()/bt_enable() cycles, and the
    // callback registration functions (bt_conn_cb_register, bt_gatt_cb_register, etc.)
    // append to linked lists without checking for duplicates. Re-registering the
    // same static structures would corrupt Zephyr's internal lists.

    mp_bluetooth_zephyr_deiniting = false;
    mp_bluetooth_zephyr_shutting_down = false;
    return 0;
}

bool mp_bluetooth_is_active(void) {
    return mp_bluetooth_zephyr_ble_state == MP_BLUETOOTH_ZEPHYR_BLE_STATE_ACTIVE;
}

void mp_bluetooth_get_current_address(uint8_t *addr_type, uint8_t *addr) {
    if (!mp_bluetooth_is_active()) {
        mp_raise_OSError(ERRNO_BLUETOOTH_NOT_ACTIVE);
    }
    bt_addr_le_t le_addr;
    size_t count = 1;
    bt_id_get(&le_addr, &count);
    if (count == 0) {
        mp_raise_OSError(EIO);
    }
    reverse_addr_byte_order(addr, &le_addr);
    *addr_type = le_addr.type;
}

void mp_bluetooth_set_address_mode(uint8_t addr_mode) {
    mp_raise_OSError(MP_EOPNOTSUPP);
}

size_t mp_bluetooth_gap_get_device_name(const uint8_t **buf) {
    const char *name = bt_get_name();
    *buf = (const uint8_t *)name;
    return strlen(name);
}

int mp_bluetooth_gap_set_device_name(const uint8_t *buf, size_t len) {
    char tmp_buf[CONFIG_BT_DEVICE_NAME_MAX + 1];
    if (len + 1 > sizeof(tmp_buf)) {
        return MP_EINVAL;
    }
    memcpy(tmp_buf, buf, len);
    tmp_buf[len] = '\0';
    return bt_err_to_errno(bt_set_name(tmp_buf));
}

// Zephyr takes advertising/scan data as an array of (type, len, payload) packets,
// and this function constructs such an array from raw advertising/scan data.
static void mp_bluetooth_prepare_bt_data(const uint8_t *data, size_t len, struct bt_data *bt_data, size_t *bt_len) {
    size_t i = 0;
    const uint8_t *d = data;
    while (d < data + len && i < *bt_len) {
        bt_data[i].type = d[1];
        bt_data[i].data_len = d[0] - 1;
        bt_data[i].data = &d[2];
        i += 1;
        d += 1 + d[0];
    }
    *bt_len = i;
}

int mp_bluetooth_gap_advertise_start(bool connectable, int32_t interval_us, const uint8_t *adv_data, size_t adv_data_len, const uint8_t *sr_data, size_t sr_data_len) {
    if (!mp_bluetooth_is_active()) {
        return ERRNO_BLUETOOTH_NOT_ACTIVE;
    }

    mp_bluetooth_gap_advertise_stop();

    if (adv_data) {
        bt_ad_len = MP_ARRAY_SIZE(bt_ad_data);
        mp_bluetooth_prepare_bt_data(adv_data, adv_data_len, bt_ad_data, &bt_ad_len);
    }

    if (sr_data) {
        bt_sd_len = MP_ARRAY_SIZE(bt_sd_data);
        mp_bluetooth_prepare_bt_data(sr_data, sr_data_len, bt_sd_data, &bt_sd_len);
    }

    struct bt_le_adv_param param = {
        .id = 0,
        .sid = 0,
        .secondary_max_skip = 0,
        .options = (connectable ? BT_LE_ADV_OPT_CONN : 0)
            | BT_LE_ADV_OPT_USE_IDENTITY
            | BT_LE_ADV_OPT_SCANNABLE,
        .interval_min = interval_us / 625,
        .interval_max = interval_us / 625 + 1, // min/max cannot be the same value
        .peer = NULL,
    };

    // pre-allocate a new connection structure as we cannot allocate this inside the connection callback
    if (mp_bt_zephyr_next_conn != NULL) {
        // This shouldn't happen - indicates previous connection didn't properly clean up
        DEBUG_printf("WARNING: mp_bt_zephyr_next_conn not NULL, resetting before allocation\n");
        mp_bt_zephyr_next_conn = NULL;
    }
    mp_bt_zephyr_next_conn = m_new0(mp_bt_zephyr_conn_t, 1);
    mp_obj_list_append(MP_STATE_PORT(bluetooth_zephyr_root_pointers)->objs_list, MP_OBJ_FROM_PTR(mp_bt_zephyr_next_conn));

    DEBUG_printf("Starting advertising: connectable=%d options=0x%x\n", connectable, param.options);
    int ret = bt_le_adv_start(&param, bt_ad_data, bt_ad_len, bt_sd_data, bt_sd_len);
    DEBUG_printf("bt_le_adv_start returned: %d\n", ret);

    return bt_err_to_errno(ret);
}

void mp_bluetooth_gap_advertise_stop(void) {
    DEBUG_printf("mp_bluetooth_gap_advertise_stop: enter, mp_bt_zephyr_next_conn=%p\n",
                 mp_bt_zephyr_next_conn);

    // Clean up pre-allocated connection object that was created for potential incoming connections
    // This prevents Zephyr's le_adv_stop_free_conn() from finding stale connection state
    if (mp_bt_zephyr_next_conn != NULL) {
        DEBUG_printf("mp_bluetooth_gap_advertise_stop: cleaning up pre-allocated connection\n");
        // Note: The object is in objs_list so will be GC'd later, just clear our reference
        mp_bt_zephyr_next_conn = NULL;
    }

    // Note: bt_le_adv_stop returns 0 if adv is already stopped.
    DEBUG_printf("mp_bluetooth_gap_advertise_stop: calling bt_le_adv_stop\n");
    int ret = bt_le_adv_stop();
    DEBUG_printf("mp_bluetooth_gap_advertise_stop: bt_le_adv_stop returned %d\n", ret);
    if (ret != 0) {
        mp_raise_OSError(bt_err_to_errno(ret));
    }
}

int mp_bluetooth_gatts_register_service_begin(bool append) {
    #if CONFIG_BT_GATT_DYNAMIC_DB

    if (!mp_bluetooth_is_active()) {
        return ERRNO_BLUETOOTH_NOT_ACTIVE;
    }

    if (append) {
        // Don't support append yet (modbluetooth.c doesn't support it yet anyway).
        return MP_EOPNOTSUPP;
    }

    // Unregister and free any previous service definitions.
    for (size_t i = 0; i < MP_STATE_PORT(bluetooth_zephyr_root_pointers)->n_services; ++i) {
        struct bt_gatt_service *service = MP_STATE_PORT(bluetooth_zephyr_root_pointers)->services[i];
        if (service != NULL) {
            bt_gatt_service_unregister(service);
            mp_bt_zephyr_free_service(service);
            MP_STATE_PORT(bluetooth_zephyr_root_pointers)->services[i] = NULL;
        }
    }
    MP_STATE_PORT(bluetooth_zephyr_root_pointers)->n_services = 0;

    // Reset the gatt characteristic value db.
    mp_bluetooth_gatts_db_reset(MP_STATE_PORT(bluetooth_zephyr_root_pointers)->gatts_db);
    MP_STATE_PORT(bluetooth_zephyr_root_pointers)->connections = NULL;
    MP_STATE_PORT(bluetooth_zephyr_root_pointers)->objs_list = mp_obj_new_list(0, NULL);
    mp_bt_zephyr_next_conn = NULL;

    return 0;

    #else
    return MP_EOPNOTSUPP;
    #endif
}

int mp_bluetooth_gatts_register_service_end(void) {
    return 0;
}

int mp_bluetooth_gatts_register_service(mp_obj_bluetooth_uuid_t *service_uuid, mp_obj_bluetooth_uuid_t **characteristic_uuids, uint16_t *characteristic_flags, mp_obj_bluetooth_uuid_t **descriptor_uuids, uint16_t *descriptor_flags, uint8_t *num_descriptors, uint16_t *handles, size_t num_characteristics) {
    #if CONFIG_BT_GATT_DYNAMIC_DB
    if (MP_STATE_PORT(bluetooth_zephyr_root_pointers)->n_services >= MP_BLUETOOTH_ZEPHYR_MAX_SERVICES) {
        return MP_E2BIG;
    }

    // first of all allocate the entire memory for all the attributes that this service is composed of
    // 1 for the service itself, 2 for each characteristic (the declaration and the value), and one for each descriptor
    size_t total_descriptors = 0;
    for (size_t i = 0; i < num_characteristics; ++i) {
        total_descriptors += num_descriptors[i];
        // we have to add the CCC manually
        if (characteristic_flags[i] & (MP_BLUETOOTH_CHARACTERISTIC_FLAG_NOTIFY | MP_BLUETOOTH_CHARACTERISTIC_FLAG_INDICATE)) {
            total_descriptors += 1;
        }
    }
    size_t total_attributes = 1 + (num_characteristics * 2) + total_descriptors;

    // allocate one extra so that we can know later where the final attribute is
    // Use malloc() to keep outside GC heap - raw pointers in objs_list aren't traced.
    struct bt_gatt_attr *svc_attributes = malloc((total_attributes + 1) * sizeof(struct bt_gatt_attr));

    size_t handle_index = 0;
    size_t descriptor_index = 0;
    size_t attr_index = 0;
    // bitfield of the handles we should ignore, should be more than enough for most applications
    uint64_t attrs_to_ignore = 0;
    uint64_t attrs_are_chrs = 0;
    uint64_t chr_has_ccc = 0;

    // Create and add service, then free the temporary UUID (gatt_db_add copies it)
    struct bt_uuid *svc_uuid = create_zephyr_uuid(service_uuid);
    add_service(svc_uuid, &svc_attributes[attr_index]);
    free(svc_uuid);
    attr_index += 1;

    for (size_t i = 0; i < num_characteristics; ++i) {

        struct add_characteristic add_char;
        add_char.uuid = create_zephyr_uuid(characteristic_uuids[i]);
        add_char.permissions = 0;
        add_char.properties = 0;
        if (characteristic_flags[i] & MP_BLUETOOTH_CHARACTERISTIC_FLAG_READ) {
            add_char.permissions |= BT_GATT_PERM_READ;
            add_char.properties |= BT_GATT_CHRC_READ;
        }
        if (characteristic_flags[i] & MP_BLUETOOTH_CHARACTERISTIC_FLAG_NOTIFY) {
            add_char.properties |= BT_GATT_CHRC_NOTIFY;
        }
        if (characteristic_flags[i] & MP_BLUETOOTH_CHARACTERISTIC_FLAG_INDICATE) {
            add_char.properties |= BT_GATT_CHRC_INDICATE;
        }
        if (characteristic_flags[i] & (MP_BLUETOOTH_CHARACTERISTIC_FLAG_WRITE | MP_BLUETOOTH_CHARACTERISTIC_FLAG_WRITE_NO_RESPONSE)) {
            add_char.permissions |= BT_GATT_PERM_WRITE;
            add_char.properties |= (BT_GATT_CHRC_WRITE | BT_GATT_CHRC_WRITE_WITHOUT_RESP);
        }
        // Security permission flags - require encryption/authentication for read/write
        if (characteristic_flags[i] & MP_BLUETOOTH_CHARACTERISTIC_FLAG_READ_ENCRYPTED) {
            add_char.permissions |= BT_GATT_PERM_READ_ENCRYPT;
        }
        if (characteristic_flags[i] & MP_BLUETOOTH_CHARACTERISTIC_FLAG_READ_AUTHENTICATED) {
            add_char.permissions |= BT_GATT_PERM_READ_AUTHEN;
        }
        if (characteristic_flags[i] & MP_BLUETOOTH_CHARACTERISTIC_FLAG_WRITE_ENCRYPTED) {
            add_char.permissions |= BT_GATT_PERM_WRITE_ENCRYPT;
        }
        if (characteristic_flags[i] & MP_BLUETOOTH_CHARACTERISTIC_FLAG_WRITE_AUTHENTICATED) {
            add_char.permissions |= BT_GATT_PERM_WRITE_AUTHEN;
        }

        add_characteristic(&add_char, &svc_attributes[attr_index], &svc_attributes[attr_index + 1]);
        // Free the temporary UUID (gatt_db_add copied it)
        free((void *)add_char.uuid);

        struct bt_gatt_attr *curr_char = &svc_attributes[attr_index];
        attrs_are_chrs |= (1 << attr_index);
        if (characteristic_flags[i] & (MP_BLUETOOTH_CHARACTERISTIC_FLAG_NOTIFY | MP_BLUETOOTH_CHARACTERISTIC_FLAG_INDICATE)) {
            chr_has_ccc |= (1 << attr_index);
        }
        attr_index += 1;
        attrs_to_ignore |= (1 << attr_index);   // ignore the value handle
        attr_index += 1;

        if (num_descriptors[i] > 0) {
            for (size_t j = 0; j < num_descriptors[i]; ++j) {

                struct add_descriptor add_desc;
                add_desc.uuid = create_zephyr_uuid(descriptor_uuids[descriptor_index]);
                add_desc.permissions = 0;
                if (descriptor_flags[descriptor_index] & MP_BLUETOOTH_CHARACTERISTIC_FLAG_READ) {
                    add_desc.permissions |= BT_GATT_PERM_READ;
                }
                if (descriptor_flags[descriptor_index] & (MP_BLUETOOTH_CHARACTERISTIC_FLAG_WRITE | MP_BLUETOOTH_CHARACTERISTIC_FLAG_WRITE_NO_RESPONSE)) {
                    add_desc.permissions |= BT_GATT_PERM_WRITE;
                }
                // Security permission flags for descriptors
                if (descriptor_flags[descriptor_index] & MP_BLUETOOTH_CHARACTERISTIC_FLAG_READ_ENCRYPTED) {
                    add_desc.permissions |= BT_GATT_PERM_READ_ENCRYPT;
                }
                if (descriptor_flags[descriptor_index] & MP_BLUETOOTH_CHARACTERISTIC_FLAG_READ_AUTHENTICATED) {
                    add_desc.permissions |= BT_GATT_PERM_READ_AUTHEN;
                }
                if (descriptor_flags[descriptor_index] & MP_BLUETOOTH_CHARACTERISTIC_FLAG_WRITE_ENCRYPTED) {
                    add_desc.permissions |= BT_GATT_PERM_WRITE_ENCRYPT;
                }
                if (descriptor_flags[descriptor_index] & MP_BLUETOOTH_CHARACTERISTIC_FLAG_WRITE_AUTHENTICATED) {
                    add_desc.permissions |= BT_GATT_PERM_WRITE_AUTHEN;
                }

                add_descriptor(curr_char, &add_desc, &svc_attributes[attr_index]);
                // Free the temporary UUID (gatt_db_add copied it)
                free((void *)add_desc.uuid);
                attr_index += 1;

                descriptor_index++;
            }
        }

        // to support indications and notifications we must add the CCC descriptor manually
        if (characteristic_flags[i] & (MP_BLUETOOTH_CHARACTERISTIC_FLAG_NOTIFY | MP_BLUETOOTH_CHARACTERISTIC_FLAG_INDICATE)) {
            struct add_descriptor add_desc;
            mp_obj_bluetooth_uuid_t ccc_uuid;
            ccc_uuid.base.type = &mp_type_bluetooth_uuid;
            ccc_uuid.data[0] = BT_UUID_GATT_CCC_VAL & 0xff;
            ccc_uuid.data[1] = (BT_UUID_GATT_CCC_VAL >> 8) & 0xff;
            ccc_uuid.type = MP_BLUETOOTH_UUID_TYPE_16;
            add_desc.uuid = create_zephyr_uuid(&ccc_uuid);
            add_desc.permissions = BT_GATT_PERM_READ | BT_GATT_PERM_WRITE;

            attrs_to_ignore |= (1 << attr_index);

            add_descriptor(curr_char, &add_desc, &svc_attributes[attr_index]);
            // Free the temporary UUID (gatt_db_add copied it)
            free((void *)add_desc.uuid);
            attr_index += 1;
        }
    }

    // Use malloc() to keep service outside GC heap.
    struct bt_gatt_service *service = malloc(sizeof(struct bt_gatt_service));
    service->attrs = svc_attributes;
    service->attr_count = attr_index;
    // invalidate the last attribute uuid pointer so that we new this is the end of attributes for this service
    svc_attributes[attr_index].uuid = NULL;

    // Note: advertising must be stopped for gatts registration to work

    int err = bt_gatt_service_register(service);
    if (err) {
        return bt_err_to_errno(err);
    }

    // now that the service has been registered, we can assign the handles for the characteristics and the descriptors
    // we are not interested in the handle of the service itself, so we start the loop from index 1
    for (size_t i = 1; i < total_attributes; i++) {
        // store all the relevant handles (characteristics and descriptors defined in Python)
        if (!((uint64_t)(attrs_to_ignore >> i) & (uint64_t)0x01)) {
            if (svc_attributes[i].user_data == NULL) {
                mp_bluetooth_gatts_db_create_entry(MP_STATE_PORT(bluetooth_zephyr_root_pointers)->gatts_db, svc_attributes[i].handle, MP_BLUETOOTH_DEFAULT_ATTR_LEN);
                mp_bluetooth_gatts_db_entry_t *entry = mp_bluetooth_gatts_db_lookup(MP_STATE_PORT(bluetooth_zephyr_root_pointers)->gatts_db, svc_attributes[i].handle);
                svc_attributes[i].user_data = entry->data;
            } else if (((uint64_t)(attrs_are_chrs >> i) & (uint64_t)0x01)) {
                if (svc_attributes[i + 1].user_data == NULL) {
                    mp_bluetooth_gatts_db_create_entry(MP_STATE_PORT(bluetooth_zephyr_root_pointers)->gatts_db, svc_attributes[i].handle, MP_BLUETOOTH_DEFAULT_ATTR_LEN);
                    mp_bluetooth_gatts_db_entry_t *entry = mp_bluetooth_gatts_db_lookup(MP_STATE_PORT(bluetooth_zephyr_root_pointers)->gatts_db, svc_attributes[i].handle);
                    svc_attributes[i + 1].user_data = entry->data;

                    if (((uint64_t)(chr_has_ccc >> i) & (uint64_t)0x01)) {
                        // create another database entry for the ccc of this characteristic
                        mp_bluetooth_gatts_db_create_entry(MP_STATE_PORT(bluetooth_zephyr_root_pointers)->gatts_db, svc_attributes[i].handle + 2, 1);
                    }
                }
            }
            handles[handle_index++] = svc_attributes[i].handle;
        }
    }

    MP_STATE_PORT(bluetooth_zephyr_root_pointers)->services[MP_STATE_PORT(bluetooth_zephyr_root_pointers)->n_services++] = service;

    return 0;

    #else
    return MP_EOPNOTSUPP;
    #endif
}

int mp_bluetooth_gap_disconnect(uint16_t conn_handle) {
    if (!mp_bluetooth_is_active()) {
        return ERRNO_BLUETOOTH_NOT_ACTIVE;
    }
    mp_bt_zephyr_conn_t *connection = mp_bt_zephyr_find_connection(conn_handle);
    if (connection) {
        return bt_conn_disconnect(connection->conn, BT_HCI_ERR_REMOTE_USER_TERM_CONN);
    }
    return MP_ENOENT;
}

int mp_bluetooth_gatts_read(uint16_t value_handle, const uint8_t **value, size_t *value_len) {
    if (!mp_bluetooth_is_active()) {
        return ERRNO_BLUETOOTH_NOT_ACTIVE;
    }
    return mp_bluetooth_gatts_db_read(MP_STATE_PORT(bluetooth_zephyr_root_pointers)->gatts_db, value_handle, value, value_len);
}

int mp_bluetooth_gatts_write(uint16_t value_handle, const uint8_t *value, size_t value_len, bool send_update) {
    if (!mp_bluetooth_is_active()) {
        return ERRNO_BLUETOOTH_NOT_ACTIVE;
    }

    int err = mp_bluetooth_gatts_db_write(MP_STATE_PORT(bluetooth_zephyr_root_pointers)->gatts_db, value_handle, value, value_len);

    if ((err == 0) && send_update) {
        struct bt_gatt_attr *attr_val = mp_bt_zephyr_find_attr_by_handle(value_handle + 1);
        mp_bluetooth_gatts_db_entry_t *ccc_entry = mp_bluetooth_gatts_db_lookup(MP_STATE_PORT(bluetooth_zephyr_root_pointers)->gatts_db, value_handle + 2);

        if (ccc_entry && (ccc_entry->data[0] == BT_GATT_CCC_NOTIFY)) {
            err = bt_gatt_notify(NULL, attr_val, value, value_len);
        } else if (ccc_entry && (ccc_entry->data[0] == BT_GATT_CCC_INDICATE)) {
            // Find a free indication params slot from the pool
            mp_bt_zephyr_indicate_params_t *ind_slot = NULL;
            for (int i = 0; i < CONFIG_BT_MAX_CONN; i++) {
                if (!mp_bt_zephyr_indicate_pool[i].in_use) {
                    ind_slot = &mp_bt_zephyr_indicate_pool[i];
                    ind_slot->in_use = true;
                    break;
                }
            }
            if (ind_slot == NULL) {
                // All slots in use - indication in progress for all connections
                err = -ENOMEM;
            } else {
                ind_slot->params.uuid = NULL;
                ind_slot->params.attr = attr_val;
                ind_slot->params.func = mp_bt_zephyr_gatt_indicate_done;
                ind_slot->params.destroy = mp_bt_zephyr_gatt_indicate_destroy;
                ind_slot->params.data = value;
                ind_slot->params.len = value_len;
                err = bt_gatt_indicate(NULL, &ind_slot->params);
                if (err != 0) {
                    // bt_gatt_indicate failed, free the slot
                    ind_slot->in_use = false;
                }
            }
        }
    }
    return err;
}

// Destroy callback frees the indication params slot back to the pool
static void mp_bt_zephyr_gatt_indicate_destroy(struct bt_gatt_indicate_params *params) {
    // Find which pool slot this params belongs to
    for (int i = 0; i < CONFIG_BT_MAX_CONN; i++) {
        if (&mp_bt_zephyr_indicate_pool[i].params == params) {
            mp_bt_zephyr_indicate_pool[i].in_use = false;
            break;
        }
    }
}

static void mp_bt_zephyr_gatt_indicate_done(struct bt_conn *conn, struct bt_gatt_indicate_params *params, uint8_t err) {
    uint16_t conn_handle = mp_bt_zephyr_conn_to_handle(conn);
    uint16_t chr_handle = params->attr->handle - 1;
    mp_bluetooth_gatts_on_indicate_complete(conn_handle, chr_handle, err);
}

static ssize_t mp_bt_zephyr_gatts_attr_read(struct bt_conn *conn, const struct bt_gatt_attr *attr, void *buf, uint16_t len, uint16_t offset) {
    uint16_t conn_handle = mp_bt_zephyr_conn_to_handle(conn);
    if (conn_handle == 0xFFFF) {
        return BT_GATT_ERR(BT_ATT_ERR_UNLIKELY);
    }

    // We receive the value handle, but to look up in the gatts db we need the
    // characteristic handle, which is the value handle minus 1.
    uint16_t _handle = attr->handle - 1;

    DEBUG_printf("BLE attr read for handle %d\n", attr->handle);

    mp_bluetooth_gatts_db_entry_t *entry = mp_bluetooth_gatts_db_lookup(MP_STATE_PORT(bluetooth_zephyr_root_pointers)->gatts_db, _handle);
    if (!entry) {
        // It could be a descriptor instead.
        _handle = attr->handle;
        entry = mp_bluetooth_gatts_db_lookup(MP_STATE_PORT(bluetooth_zephyr_root_pointers)->gatts_db, _handle);
        if (!entry) {
            return BT_GATT_ERR(BT_ATT_ERR_INVALID_HANDLE);
        }
    }

    // Notify Python - allows dynamic value update or rejection.
    mp_int_t result = mp_bluetooth_gatts_on_read_request(conn_handle, _handle);
    if (result != 0) {
        return BT_GATT_ERR(result);
    }

    // Re-lookup in case Python modified the value via gatts_write.
    entry = mp_bluetooth_gatts_db_lookup(MP_STATE_PORT(bluetooth_zephyr_root_pointers)->gatts_db, _handle);
    if (!entry) {
        return BT_GATT_ERR(BT_ATT_ERR_INVALID_HANDLE);
    }

    return bt_gatt_attr_read(conn, attr, buf, len, offset, entry->data, entry->data_len);
}

static ssize_t mp_bt_zephyr_gatts_attr_write(struct bt_conn *conn, const struct bt_gatt_attr *attr, const void *buf, uint16_t len, uint16_t offset, uint8_t flags) {
    uint16_t conn_handle = mp_bt_zephyr_conn_to_handle(conn);

    DEBUG_printf("BLE attr write for handle %d\n", attr->handle);

    // the characteristic handle is the value handle minus 1
    uint16_t _handle = attr->handle - 1;

    mp_bluetooth_gatts_db_entry_t *entry = mp_bluetooth_gatts_db_lookup(MP_STATE_PORT(bluetooth_zephyr_root_pointers)->gatts_db, _handle);
    if (!entry) {
        // it could be a descriptor instead
        _handle = attr->handle;
        entry = mp_bluetooth_gatts_db_lookup(MP_STATE_PORT(bluetooth_zephyr_root_pointers)->gatts_db, _handle);
        if (!entry) {
            return BT_GATT_ERR(BT_ATT_ERR_INVALID_HANDLE);
        }
    }

    // Don't write anything if prepare flag is set
    if (flags & BT_GATT_WRITE_FLAG_PREPARE) {
        return 0;
    }

    if (offset > entry->data_alloc) {
        return BT_GATT_ERR(BT_ATT_ERR_INVALID_OFFSET);
    }

    if ((offset + len) > entry->data_alloc) {
        return BT_GATT_ERR(BT_ATT_ERR_INVALID_ATTRIBUTE_LEN);
    }

    if (entry->append) {
        offset = entry->data_len;
    }

    // copy the data into the buffer in the gatts database
    memcpy(&entry->data[offset], buf, len);
    entry->data_len = offset + len;

    mp_bluetooth_gatts_on_write(conn_handle, _handle);

    return len;
}

static struct bt_gatt_attr *mp_bt_zephyr_find_attr_by_handle(uint16_t value_handle) {
    for (size_t i = 0; i < MP_STATE_PORT(bluetooth_zephyr_root_pointers)->n_services; i++) {
        int j = 0;
        while (MP_STATE_PORT(bluetooth_zephyr_root_pointers)->services[i]->attrs[j].uuid != NULL) {
            if (MP_STATE_PORT(bluetooth_zephyr_root_pointers)->services[i]->attrs[j].handle == value_handle) {
                return &MP_STATE_PORT(bluetooth_zephyr_root_pointers)->services[i]->attrs[j];
            }
            j++;
        }
    }
    return NULL;
}

int mp_bluetooth_gatts_notify_indicate(uint16_t conn_handle, uint16_t value_handle, int gatts_op, const uint8_t *value, size_t value_len) {
    if (!mp_bluetooth_is_active()) {
        return ERRNO_BLUETOOTH_NOT_ACTIVE;
    }

    // If no data provided, read from the characteristic database.
    // This matches the behavior of NimBLE and BTstack.
    if (value == NULL || value_len == 0) {
        mp_bluetooth_gatts_db_entry_t *entry = mp_bluetooth_gatts_db_lookup(
            MP_STATE_PORT(bluetooth_zephyr_root_pointers)->gatts_db, value_handle);
        if (entry != NULL) {
            value = entry->data;
            value_len = entry->data_len;
        }
    }

    int err = MP_ENOENT;
    mp_bt_zephyr_conn_t *connection = mp_bt_zephyr_find_connection(conn_handle);

    if (connection) {
        // Look up attr by value_handle directly. This works for both:
        // - Declaration handles (from gatts_register_services): bt_gatt_notify_cb
        //   detects CHRC UUID and auto-adjusts to value handle internally.
        // - GATTC-discovered value handles (e.g. perf_gatt_notify.py): used directly.
        // If not in local GATT DB, falls through to raw ATT fallback below.
        struct bt_gatt_attr *attr_val = mp_bt_zephyr_find_attr_by_handle(value_handle);

        if (attr_val) {
            switch (gatts_op) {
                case MP_BLUETOOTH_GATTS_OP_NOTIFY: {
                    err = bt_gatt_notify(connection->conn, attr_val, value, value_len);
                    // Process work queue to ensure notification is sent immediately.
                    // This is critical on platforms without FreeRTOS (e.g., STM32WB55)
                    // where the work queue isn't processed asynchronously.
                    mp_bluetooth_zephyr_work_process();
                    break;
                }
                case MP_BLUETOOTH_GATTS_OP_INDICATE: {
                    // Find a free indication params slot from the pool
                    mp_bt_zephyr_indicate_params_t *ind_slot = NULL;
                    for (int i = 0; i < CONFIG_BT_MAX_CONN; i++) {
                        if (!mp_bt_zephyr_indicate_pool[i].in_use) {
                            ind_slot = &mp_bt_zephyr_indicate_pool[i];
                            ind_slot->in_use = true;
                            break;
                        }
                    }
                    if (ind_slot == NULL) {
                        err = -ENOMEM;
                    } else {
                        ind_slot->params.uuid = NULL;
                        ind_slot->params.attr = attr_val;
                        ind_slot->params.func = mp_bt_zephyr_gatt_indicate_done;
                        ind_slot->params.destroy = mp_bt_zephyr_gatt_indicate_destroy;
                        ind_slot->params.data = value;
                        ind_slot->params.len = value_len;
                        err = bt_gatt_indicate(connection->conn, &ind_slot->params);
                        if (err != 0) {
                            ind_slot->in_use = false;
                        }
                    }
                    break;
                }
            }
        }
        if (!attr_val && gatts_op == MP_BLUETOOTH_GATTS_OP_NOTIFY) {
            // Handle not in local GATT DB — send raw ATT notification PDU.
            // This supports cases where a GATTC-discovered remote handle is
            // passed to gatts_notify (e.g. perf_gatt_notify.py).
            struct net_buf *buf = bt_att_create_pdu(connection->conn, BT_ATT_OP_NOTIFY,
                sizeof(struct bt_att_notify) + value_len);
            if (buf) {
                struct bt_att_notify *nfy = net_buf_add(buf,
                    sizeof(struct bt_att_notify) + value_len);
                nfy->handle = sys_cpu_to_le16(value_handle);
                memcpy(nfy->value, value, value_len);
                bt_att_set_tx_meta_data(buf, NULL, NULL, BT_ATT_CHAN_OPT_NONE);
                err = bt_att_send(connection->conn, buf);
            } else {
                err = -ENOMEM;
            }
            mp_bluetooth_zephyr_work_process();
        }
    }

    return err;
}

int mp_bluetooth_gatts_set_buffer(uint16_t value_handle, size_t len, bool append) {
    if (!mp_bluetooth_is_active()) {
        return ERRNO_BLUETOOTH_NOT_ACTIVE;
    }
    return mp_bluetooth_gatts_db_resize(MP_STATE_PORT(bluetooth_zephyr_root_pointers)->gatts_db, value_handle, len, append);
}

int mp_bluetooth_get_preferred_mtu(void) {
    if (!mp_bluetooth_is_active()) {
        mp_raise_OSError(ERRNO_BLUETOOTH_NOT_ACTIVE);
    }
    // Return the compile-time configured L2CAP TX MTU
    // This is the maximum MTU that will be proposed during MTU exchange
    return CONFIG_BT_L2CAP_TX_MTU;
}

int mp_bluetooth_set_preferred_mtu(uint16_t mtu) {
    if (!mp_bluetooth_is_active()) {
        return ERRNO_BLUETOOTH_NOT_ACTIVE;
    }
    // Zephyr's preferred MTU is determined by CONFIG_BT_L2CAP_TX_MTU at compile time.
    // Zephyr hardcodes the proposed MTU in gatt_exchange_mtu_encode() to BT_LOCAL_ATT_MTU_UATT,
    // which derives from CONFIG_BT_L2CAP_TX_MTU. There's no runtime API to change this without
    // modifying Zephyr's gatt.c (violates "don't modify Zephyr submodule" constraint).
    //
    // For now, reject all runtime MTU configuration. To change the preferred MTU, adjust
    // CONFIG_BT_L2CAP_TX_MTU in zephyr_ble_config.h and rebuild.
    (void)mtu;
    return MP_EOPNOTSUPP;
}

#if MICROPY_PY_BLUETOOTH_ENABLE_CENTRAL_MODE

int mp_bluetooth_gap_scan_start(int32_t duration_ms, int32_t interval_us, int32_t window_us, bool active_scan) {
    DEBUG_printf("gap_scan_start: dur=%d\n", (int)duration_ms);

    // Stop any ongoing GAP scan.
    int ret = mp_bluetooth_gap_scan_stop();
    if (ret) {
        return ret;
    }

    struct bt_le_scan_param param = {
        .type = active_scan ? BT_HCI_LE_SCAN_ACTIVE : BT_HCI_LE_SCAN_PASSIVE,
        .options = BT_LE_SCAN_OPT_NONE,
        .interval = MAX(BLE_HCI_SCAN_ITVL_MIN, MIN(BLE_HCI_SCAN_ITVL_MAX, interval_us / 625)),
        .window = MAX(BLE_HCI_SCAN_WINDOW_MIN, MIN(BLE_HCI_SCAN_WINDOW_MAX, window_us / 625)),
    };

    // Drain pending work items (connection cleanup etc) before starting scan.
    mp_bluetooth_zephyr_work_process();

    int err = bt_le_scan_start(&param, NULL);
    DEBUG_printf("gap_scan_start: err=%d\n", err);
    if (err != 0) {
        return bt_err_to_errno(err);
    }
    k_timer_start(&mp_bluetooth_zephyr_gap_scan_timer, K_MSEC(duration_ms), K_NO_WAIT);
    mp_bluetooth_zephyr_gap_scan_state = MP_BLUETOOTH_ZEPHYR_GAP_SCAN_STATE_ACTIVE;
    return 0;
}

int mp_bluetooth_gap_scan_stop(void) {
    DEBUG_printf("mp_bluetooth_gap_scan_stop\n");
    if (!mp_bluetooth_is_active()) {
        return ERRNO_BLUETOOTH_NOT_ACTIVE;
    }
    if (mp_bluetooth_zephyr_gap_scan_state == MP_BLUETOOTH_ZEPHYR_GAP_SCAN_STATE_INACTIVE) {
        // Already stopped.
        return 0;
    }
    mp_bluetooth_zephyr_gap_scan_state = MP_BLUETOOTH_ZEPHYR_GAP_SCAN_STATE_INACTIVE;
    k_timer_stop(&mp_bluetooth_zephyr_gap_scan_timer);
    int err = bt_le_scan_stop();
    if (err == 0) {
        mp_bluetooth_gap_on_scan_complete();
        return 0;
    }
    return bt_err_to_errno(err);
}

int mp_bluetooth_gap_peripheral_connect(uint8_t addr_type, const uint8_t *addr, int32_t duration_ms, int32_t min_conn_interval_us, int32_t max_conn_interval_us) {
    DEBUG_printf("mp_bluetooth_gap_peripheral_connect: addr_type=%u duration_ms=%d\n", addr_type, (int)duration_ms);
    DEBUG_printf("  addr=%02x:%02x:%02x:%02x:%02x:%02x (BE from MicroPython)\n",
                 addr[0], addr[1], addr[2], addr[3], addr[4], addr[5]);
    if (!mp_bluetooth_is_active()) {
        return ERRNO_BLUETOOTH_NOT_ACTIVE;
    }

    // Stop scanning if active (can't scan and initiate connection simultaneously)
    if (mp_bluetooth_zephyr_gap_scan_state != MP_BLUETOOTH_ZEPHYR_GAP_SCAN_STATE_INACTIVE) {
        DEBUG_printf("  stopping active scan before connect\n");
        mp_bluetooth_gap_scan_stop();
    }

    // Convert MicroPython address (BE byte order) to Zephyr address (LE byte order)
    bt_addr_le_t peer_addr;
    peer_addr.type = addr_type;
    for (int i = 0; i < 6; ++i) {
        peer_addr.a.val[i] = addr[5 - i];  // Reverse byte order: BE -> LE
    }
    DEBUG_printf("  peer_addr: type=%d addr=%02x:%02x:%02x:%02x:%02x:%02x (LE for Zephyr)\n",
                 peer_addr.type,
                 peer_addr.a.val[5], peer_addr.a.val[4], peer_addr.a.val[3],
                 peer_addr.a.val[2], peer_addr.a.val[1], peer_addr.a.val[0]);

    // Create connection parameters for scanning during connection establishment
    struct bt_conn_le_create_param create_param = {
        .options = 0,
        .interval = BT_GAP_SCAN_FAST_INTERVAL,     // 0x0060 = 60ms in 0.625ms units
        .window = BT_GAP_SCAN_FAST_INTERVAL,       // Same = continuous scanning
        .interval_coded = 0,
        .window_coded = 0,
        .timeout = duration_ms > 0 ? (duration_ms / 10) : (CONFIG_BT_CREATE_CONN_TIMEOUT / 10), // Convert ms to 10ms units
    };

    // Create connection interval parameters
    uint16_t interval_min = min_conn_interval_us > 0
        ? BT_GAP_US_TO_CONN_INTERVAL(min_conn_interval_us)
        : BT_GAP_INIT_CONN_INT_MIN;  // 0x0018 = 30ms in 1.25ms units
    uint16_t interval_max = max_conn_interval_us > 0
        ? BT_GAP_US_TO_CONN_INTERVAL(max_conn_interval_us)
        : BT_GAP_INIT_CONN_INT_MAX;  // 0x0028 = 50ms in 1.25ms units

    struct bt_le_conn_param conn_param = {
        .interval_min = interval_min,
        .interval_max = interval_max,
        .latency = 0,
        .timeout = BT_GAP_MS_TO_CONN_TIMEOUT(4000),  // 4 seconds in 10ms units
    };

    DEBUG_printf("  create_param: interval=%d window=%d timeout=%d\n",
                 create_param.interval, create_param.window, create_param.timeout);
    DEBUG_printf("  conn_param: interval_min=%d interval_max=%d latency=%d timeout=%d\n",
                 conn_param.interval_min, conn_param.interval_max,
                 conn_param.latency, conn_param.timeout);

    // Process any pending work items before attempting connection.
    // This ensures Zephyr has finished cleaning up any previous connection to
    // the same peer address. Zephyr releases its final connection reference
    // AFTER the disconnected callback returns via a work item.
    mp_bluetooth_zephyr_work_process();

    // Pre-allocate connection tracking structure
    if (mp_bt_zephyr_next_conn != NULL) {
        // This shouldn't happen - indicates previous connection didn't properly clean up
        DEBUG_printf("WARNING: mp_bt_zephyr_next_conn not NULL, resetting before allocation\n");
        mp_bt_zephyr_next_conn = NULL;
    }
    mp_bt_zephyr_next_conn = m_new0(mp_bt_zephyr_conn_t, 1);
    mp_obj_list_append(MP_STATE_PORT(bluetooth_zephyr_root_pointers)->objs_list, MP_OBJ_FROM_PTR(mp_bt_zephyr_next_conn));

    // Initiate connection
    struct bt_conn *conn = NULL;  // Must be NULL for Zephyr's check
    DEBUG_printf("  calling bt_conn_le_create...\n");
    int err = bt_conn_le_create(&peer_addr, &create_param, &conn_param, &conn);

    if (err != 0) {
        // Connection initiation failed - structure registered with GC, just reset pointer
        DEBUG_printf("  bt_conn_le_create failed: err=%d\n", err);
        // Log specific Zephyr error meanings
        if (err == -EINVAL) {
            DEBUG_printf("  EINVAL: invalid params, bad random addr, or conn exists\n");
        } else if (err == -EAGAIN) {
            DEBUG_printf("  EAGAIN: BT dev not ready or scanner blocking\n");
        } else if (err == -EALREADY) {
            DEBUG_printf("  EALREADY: already initiating a connection\n");
        } else if (err == -ENOMEM) {
            DEBUG_printf("  ENOMEM: no memory for connection\n");
        }
        mp_bt_zephyr_next_conn = NULL;
        return bt_err_to_errno(err);
    }

    // Handle reference management for the connection.
    // bt_conn_le_create() returns with a reference to the connection object.
    //
    // On platforms with async HCI (RP2 FreeRTOS): bt_conn_le_create returns,
    // we store conn, then callback fires later and uses our stored ref.
    //
    // On platforms with sync HCI (STM32WB without FreeRTOS): callback may fire
    // DURING bt_conn_le_create before it returns. In this case:
    // - Callback sees mp_bt_zephyr_next_conn->conn == NULL
    // - Callback takes its own ref (correct - callback param is borrowed)
    // - Callback sets mp_bt_zephyr_next_conn = NULL
    // - bt_conn_le_create returns with conn (another ref)
    // - We must unref the extra ref since callback already handled it
    DEBUG_printf("  bt_conn_le_create succeeded, conn=%p\n", conn);

    if (mp_bt_zephyr_next_conn == NULL) {
        // Callback already fired synchronously and handled the connection.
        // The callback took its own reference, so we must release the
        // reference returned by bt_conn_le_create to avoid ref leak.
        DEBUG_printf("  callback handled synchronously, unref extra ref\n");
        bt_conn_unref(conn);
    } else {
        // Normal async path - store the reference for callback to use.
        mp_bt_zephyr_next_conn->conn = conn;
    }

    return 0;
}

int mp_bluetooth_gap_peripheral_connect_cancel(void) {
    DEBUG_printf("mp_bluetooth_gap_peripheral_connect_cancel\n");
    if (!mp_bluetooth_is_active()) {
        return ERRNO_BLUETOOTH_NOT_ACTIVE;
    }

    // Check if there's a pending outgoing connection
    if (mp_bt_zephyr_next_conn == NULL || mp_bt_zephyr_next_conn->conn == NULL) {
        DEBUG_printf("  No pending connection to cancel\n");
        return MP_EINVAL;  // No connection in progress
    }

    // Disconnect the pending connection
    // This will trigger the connected callback with BT_HCI_ERR_UNKNOWN_CONN_ID error
    DEBUG_printf("  Cancelling connection %p\n", mp_bt_zephyr_next_conn->conn);
    int err = bt_conn_disconnect(mp_bt_zephyr_next_conn->conn, BT_HCI_ERR_REMOTE_USER_TERM_CONN);

    // Note: Don't unref here - the connected callback will handle cleanup on failure
    return bt_err_to_errno(err);
}

#endif // MICROPY_PY_BLUETOOTH_ENABLE_CENTRAL_MODE

// Note: modbluetooth UUIDs store their data in LE.
// UUIDs are allocated with malloc() to keep them outside GC-managed heap.
// GC cannot trace raw pointers stored via MP_OBJ_FROM_PTR() in objs_list.
static struct bt_uuid *create_zephyr_uuid(const mp_obj_bluetooth_uuid_t *uuid) {
    struct bt_uuid *result = (struct bt_uuid *)malloc(sizeof(union uuid_u));
    if (uuid->type == MP_BLUETOOTH_UUID_TYPE_16) {
        bt_uuid_create(result, uuid->data, 2);
    } else if (uuid->type == MP_BLUETOOTH_UUID_TYPE_32) {
        bt_uuid_create(result, uuid->data, 4);
    } else {    //  MP_BLUETOOTH_UUID_TYPE_128
        bt_uuid_create(result, uuid->data, 16);
    }
    return result;
}

// GATT callback for MTU updates (handles both local and remote-initiated MTU exchange)
// This callback is registered via bt_gatt_cb_register() and fires whenever the ATT MTU changes.
static void mp_bt_zephyr_gatt_mtu_updated(struct bt_conn *conn, uint16_t tx, uint16_t rx) {
    DEBUG_printf("gatt_mtu_updated: tx=%d rx=%d\n", tx, rx);

    if (!mp_bluetooth_is_active()) {
        return;
    }

    // Only notify Python if the connection is already tracked.
    // This ensures _IRQ_MTU_EXCHANGED fires after _IRQ_CENTRAL_CONNECT/_IRQ_PERIPHERAL_CONNECT.
    // Zephyr may fire this callback before our connection callback runs, so we silently
    // ignore early MTU updates - Python can query the MTU later if needed.
    uint8_t conn_handle = mp_bt_zephyr_conn_to_handle(conn);
    if (conn_handle == 0xFF) {
        DEBUG_printf("gatt_mtu_updated: ignoring (connection not yet tracked)\n");
        return;
    }

    // Effective MTU is the minimum of TX and RX MTU values
    uint16_t mtu = MIN(tx, rx);

    mp_bluetooth_gatts_on_mtu_exchanged(conn_handle, mtu);
}

// GATT callback structure for MTU updates
struct bt_gatt_cb mp_bt_zephyr_gatt_callbacks = {
    .att_mtu_updated = mp_bt_zephyr_gatt_mtu_updated,
};

#if MICROPY_PY_BLUETOOTH_ENABLE_GATT_CLIENT
// Convert Zephyr UUID to MicroPython UUID format.
// Note: modbluetooth UUIDs store their data in LE.
static mp_obj_bluetooth_uuid_t zephyr_uuid_to_mp(const struct bt_uuid *uuid) {
    mp_obj_bluetooth_uuid_t result;
    result.base.type = &mp_type_bluetooth_uuid;
    switch (uuid->type) {
        case BT_UUID_TYPE_16: {
            const struct bt_uuid_16 *u16 = (const struct bt_uuid_16 *)uuid;
            result.type = MP_BLUETOOTH_UUID_TYPE_16;
            result.data[0] = u16->val & 0xff;
            result.data[1] = (u16->val >> 8) & 0xff;
            break;
        }
        case BT_UUID_TYPE_32: {
            const struct bt_uuid_32 *u32 = (const struct bt_uuid_32 *)uuid;
            result.type = MP_BLUETOOTH_UUID_TYPE_32;
            result.data[0] = u32->val & 0xff;
            result.data[1] = (u32->val >> 8) & 0xff;
            result.data[2] = (u32->val >> 16) & 0xff;
            result.data[3] = (u32->val >> 24) & 0xff;
            break;
        }
        case BT_UUID_TYPE_128: {
            const struct bt_uuid_128 *u128 = (const struct bt_uuid_128 *)uuid;
            result.type = MP_BLUETOOTH_UUID_TYPE_128;
            memcpy(result.data, u128->val, 16);
            break;
        }
        default:
            // Should not happen - set to invalid state
            result.type = 0;
            break;
    }
    return result;
}

#endif // MICROPY_PY_BLUETOOTH_ENABLE_GATT_CLIENT

// Get bt_conn pointer from connection handle, returns NULL if not found.
// Used by both GATT client and pairing/bonding operations
static struct bt_conn *mp_bt_zephyr_get_conn(uint16_t conn_handle) {
    mp_bt_zephyr_conn_t *connection = mp_bt_zephyr_find_connection(conn_handle);
    return connection ? connection->conn : NULL;
}

static void gatt_db_add(const struct bt_gatt_attr *pattern, struct bt_gatt_attr *attr, size_t user_data_len) {
    const union uuid_u *u = CONTAINER_OF(pattern->uuid, union uuid_u, uuid);
    size_t uuid_size = sizeof(u->u16);

    if (u->uuid.type == BT_UUID_TYPE_32) {
        uuid_size = sizeof(u->u32);
    } else if (u->uuid.type == BT_UUID_TYPE_128) {
        uuid_size = sizeof(u->u128);
    }

    memcpy(attr, pattern, sizeof(*attr));

    // Store the UUID - use malloc() to keep outside GC heap.
    // GC cannot trace raw pointers stored in objs_list.
    attr->uuid = (const struct bt_uuid *)malloc(sizeof(union uuid_u));
    memcpy((void *)attr->uuid, &u->uuid, uuid_size);

    // Copy user_data to the buffer - use malloc() to keep outside GC heap.
    if (user_data_len) {
        attr->user_data = malloc(user_data_len);
        memcpy(attr->user_data, pattern->user_data, user_data_len);
    }
}

static void add_service(const struct bt_uuid *u, struct bt_gatt_attr *attr) {
    union uuid_u *uuid = (union uuid_u *)u;

    size_t uuid_size = sizeof(uuid->u16);

    if (uuid->uuid.type == BT_UUID_TYPE_32) {
        uuid_size = sizeof(uuid->u32);
    } else if (uuid->uuid.type == BT_UUID_TYPE_128) {
        uuid_size = sizeof(uuid->u128);
    }

    gatt_db_add(&(struct bt_gatt_attr)BT_GATT_PRIMARY_SERVICE(&uuid->uuid), attr, uuid_size);
}

static void add_characteristic(struct add_characteristic *ch, struct bt_gatt_attr *attr_chrc, struct bt_gatt_attr *attr_value) {
    struct bt_gatt_chrc *chrc_data;

    // Add Characteristic Declaration
    gatt_db_add(&(struct bt_gatt_attr)
        BT_GATT_ATTRIBUTE(BT_UUID_GATT_CHRC,
            BT_GATT_PERM_READ,
            bt_gatt_attr_read_chrc, NULL,
            (&(struct bt_gatt_chrc) {})), attr_chrc, sizeof(*chrc_data));

    // Allow prepare writes
    ch->permissions |= BT_GATT_PERM_PREPARE_WRITE;

    // Add Characteristic Value
    gatt_db_add(&(struct bt_gatt_attr)
        BT_GATT_ATTRIBUTE(ch->uuid,
            ch->permissions & GATT_PERM_MASK,
            mp_bt_zephyr_gatts_attr_read, mp_bt_zephyr_gatts_attr_write, NULL), attr_value, 0);

    chrc_data = attr_chrc->user_data;
    chrc_data->properties = ch->properties;
    chrc_data->uuid = attr_value->uuid;
}

static void ccc_cfg_changed(const struct bt_gatt_attr *attr, uint16_t value) {
    mp_bluetooth_gatts_db_entry_t *entry = mp_bluetooth_gatts_db_lookup(MP_STATE_PORT(bluetooth_zephyr_root_pointers)->gatts_db, attr->handle);
    entry->data[0] = value;
}

static struct bt_gatt_attr ccc_definition = BT_GATT_CCC(ccc_cfg_changed, BT_GATT_PERM_READ | BT_GATT_PERM_WRITE);

static void add_ccc(struct bt_gatt_attr *attr, struct bt_gatt_attr *attr_desc) {
    struct bt_gatt_chrc *chrc = attr->user_data;

    // Check characteristic properties
    if (!(chrc->properties & (BT_GATT_CHRC_NOTIFY | BT_GATT_CHRC_INDICATE))) {
        mp_raise_OSError(MP_EINVAL);
    }

    // Add CCC descriptor to GATT database
    gatt_db_add(&ccc_definition, attr_desc, 0);
}

static void add_cep(const struct bt_gatt_attr *attr_chrc, struct bt_gatt_attr *attr_desc) {
    struct bt_gatt_chrc *chrc = attr_chrc->user_data;
    struct bt_gatt_cep cep_value;

    // Extended Properties bit shall be set
    if (!(chrc->properties & BT_GATT_CHRC_EXT_PROP)) {
        mp_raise_OSError(MP_EINVAL);
    }

    cep_value.properties = 0x0000;

    // Add CEP descriptor to GATT database
    gatt_db_add(&(struct bt_gatt_attr)BT_GATT_CEP(&cep_value), attr_desc, sizeof(cep_value));
}

static void add_descriptor(struct bt_gatt_attr *chrc, struct add_descriptor *d, struct bt_gatt_attr *attr_desc) {
    if (!bt_uuid_cmp(d->uuid, BT_UUID_GATT_CEP)) {
        add_cep(chrc, attr_desc);
    } else if (!bt_uuid_cmp(d->uuid, BT_UUID_GATT_CCC)) {
        add_ccc(chrc, attr_desc);
    } else {
        // Allow prepare writes
        d->permissions |= BT_GATT_PERM_PREPARE_WRITE;

        gatt_db_add(&(struct bt_gatt_attr)
            BT_GATT_DESCRIPTOR(d->uuid,
                d->permissions & GATT_PERM_MASK,
                mp_bt_zephyr_gatts_attr_read, mp_bt_zephyr_gatts_attr_write, NULL), attr_desc, 0);
    }
}

// Free all memory associated with a GATT service.
// Called when unregistering services to prevent memory leaks.
//
// Memory allocation pattern in GATT registration:
// - gatt_db_add() allocates attr->uuid via malloc for ALL attributes
// - gatt_db_add() allocates attr->user_data via malloc ONLY when user_data_len > 0:
//   - Service declaration (index 0): user_data = malloc'd copy of service UUID
//   - Characteristic declaration: user_data = malloc'd bt_gatt_chrc struct
//   - Other attrs: user_data is either NULL or later assigned to gatts_db entry (GC heap)
// - Service struct and attrs array are malloc'd in mp_bluetooth_gatts_register_service
//
// We must NOT free user_data that points to gatts_db entries (GC managed).
// We identify characteristic declarations by their read callback (bt_gatt_attr_read_chrc).
static void mp_bt_zephyr_free_service(struct bt_gatt_service *service) {
    if (service == NULL) {
        return;
    }

    if (service->attrs != NULL) {
        // First: free user_data for service declaration (index 0) and characteristic declarations
        // Service declaration is always at index 0
        if (service->attr_count > 0 && service->attrs[0].user_data != NULL) {
            free(service->attrs[0].user_data);
        }

        // Characteristic declarations have read callback = bt_gatt_attr_read_chrc
        extern ssize_t bt_gatt_attr_read_chrc(struct bt_conn *conn,
            const struct bt_gatt_attr *attr, void *buf, uint16_t len, uint16_t offset);
        for (size_t i = 1; i < service->attr_count; i++) {
            struct bt_gatt_attr *attr = &service->attrs[i];
            if (attr->read == bt_gatt_attr_read_chrc && attr->user_data != NULL) {
                free(attr->user_data);
            }
        }

        // Second: free all UUIDs (all were malloc'd by gatt_db_add)
        for (size_t i = 0; i < service->attr_count; i++) {
            if (service->attrs[i].uuid != NULL) {
                free((void *)service->attrs[i].uuid);
            }
        }

        // Free the attributes array itself
        free(service->attrs);
    }

    // Free the service struct itself
    free(service);
}

// GATT Client implementation
#if MICROPY_PY_BLUETOOTH_ENABLE_GATT_CLIENT

// Service discovery callback
static uint8_t gattc_service_discover_cb(struct bt_conn *conn,
    const struct bt_gatt_attr *attr, struct bt_gatt_discover_params *params) {

    if (!mp_bluetooth_is_active()) {
        return BT_GATT_ITER_STOP;
    }

    uint16_t conn_handle = MP_STATE_PORT(bluetooth_zephyr_root_pointers)->gattc_discover_conn_handle;

    if (attr == NULL) {
        // Discovery complete
        mp_bluetooth_gattc_on_discover_complete(MP_BLUETOOTH_IRQ_GATTC_SERVICE_DONE, conn_handle, 0);
        return BT_GATT_ITER_STOP;
    }

    // Extract service info from attribute
    const struct bt_gatt_service_val *svc = (const struct bt_gatt_service_val *)attr->user_data;
    mp_obj_bluetooth_uuid_t service_uuid = zephyr_uuid_to_mp(svc->uuid);

    // Report service: start_handle from attr->handle, end_handle from service_val
    mp_bluetooth_gattc_on_primary_service_result(conn_handle, attr->handle, svc->end_handle, &service_uuid);

    return BT_GATT_ITER_CONTINUE;
}

// Characteristic discovery callback
static uint8_t gattc_characteristic_discover_cb(struct bt_conn *conn,
    const struct bt_gatt_attr *attr, struct bt_gatt_discover_params *params) {

    if (!mp_bluetooth_is_active()) {
        return BT_GATT_ITER_STOP;
    }

    mp_bluetooth_zephyr_root_pointers_t *rp = MP_STATE_PORT(bluetooth_zephyr_root_pointers);
    uint16_t conn_handle = rp->gattc_discover_conn_handle;

    // If there's a pending characteristic, emit it now (we now know its end_handle)
    if (rp->gattc_pending_char.pending) {
        rp->gattc_pending_char.pending = false;

        // end_handle is either one before current char's def_handle, or the discovery end_handle
        uint16_t end_handle = rp->gattc_discover_end_handle;
        if (attr != NULL) {
            end_handle = attr->handle - 1;
        }

        DEBUG_printf("gattc_char_discover: value_handle=0x%04x end_handle=0x%04x props=0x%02x\n",
            rp->gattc_pending_char.value_handle, end_handle, rp->gattc_pending_char.properties);

        mp_bluetooth_gattc_on_characteristic_result(conn_handle,
            rp->gattc_pending_char.value_handle,
            end_handle,
            rp->gattc_pending_char.properties,
            &rp->gattc_pending_char.uuid);

        // Auto-register subscription for characteristics with NOTIFY/INDICATE properties.
        // This allows notifications to be delivered without explicit CCCD write,
        // matching NimBLE's behavior where notifications are always delivered.
        gattc_register_auto_subscription(conn, conn_handle,
            rp->gattc_pending_char.value_handle,
            rp->gattc_pending_char.properties);
    }

    if (attr == NULL) {
        // Discovery complete
        mp_bluetooth_gattc_on_discover_complete(MP_BLUETOOTH_IRQ_GATTC_CHARACTERISTIC_DONE, conn_handle, 0);
        return BT_GATT_ITER_STOP;
    }

    // Extract characteristic info
    const struct bt_gatt_chrc *chrc = (const struct bt_gatt_chrc *)attr->user_data;
    mp_obj_bluetooth_uuid_t char_uuid = zephyr_uuid_to_mp(chrc->uuid);

    // Buffer this characteristic - we'll emit it when we see the next one (to get end_handle)
    rp->gattc_pending_char.value_handle = chrc->value_handle;
    rp->gattc_pending_char.def_handle = attr->handle;
    rp->gattc_pending_char.properties = chrc->properties;
    rp->gattc_pending_char.uuid = char_uuid;
    rp->gattc_pending_char.pending = true;

    return BT_GATT_ITER_CONTINUE;
}

// Subscription complete callback (called when CCCD write completes)
// Note: Zephyr calls this from gatt_write_ccc_rsp for BOTH subscribe and
// unsubscribe CCCD writes. We only want to fire WRITE_DONE for explicit
// bt_gatt_subscribe calls, not for bt_gatt_unsubscribe completions.
static void gattc_subscribe_cb(struct bt_conn *conn, uint8_t err,
    struct bt_gatt_subscribe_params *params) {

    DEBUG_printf("gattc_subscribe_cb: err=%d ccc_handle=0x%04x value=0x%04x\n",
                 err, params->ccc_handle, params->value);

    if (!mp_bluetooth_is_active()) {
        return;
    }

    mp_bluetooth_zephyr_root_pointers_t *rp = MP_STATE_PORT(bluetooth_zephyr_root_pointers);

    // Only process if we explicitly called bt_gatt_subscribe.
    // Zephyr also calls this callback from unsubscribe CCCD write responses
    // (gatt_write_ccc_rsp calls params->subscribe for all CCCD writes).
    // Without this guard, unsubscribe operations generate spurious WRITE_DONE.
    if (!rp->gattc_subscribe_pending) {
        return;
    }
    rp->gattc_subscribe_pending = false;

    // Clear subscription-changing flag (new subscription is now set up)
    rp->gattc_subscribe_changing = false;

    // Mark subscription as active/inactive based on result
    rp->gattc_subscribe_active = (err == 0);

    // Fire WRITE_DONE callback for the CCCD write
    mp_bluetooth_gattc_on_read_write_status(MP_BLUETOOTH_IRQ_GATTC_WRITE_DONE,
                                             rp->gattc_subscribe_conn_handle,
                                             rp->gattc_subscribe_ccc_handle,
                                             err);
}

// Notification/Indication callback
static uint8_t gattc_notify_cb(struct bt_conn *conn,
    struct bt_gatt_subscribe_params *params,
    const void *data, uint16_t length) {

    DEBUG_printf("gattc_notify_cb: data=%p length=%d value=0x%04x\n", data, length, params->value);

    if (!mp_bluetooth_is_active()) {
        return BT_GATT_ITER_STOP;
    }

    mp_bluetooth_zephyr_root_pointers_t *rp = MP_STATE_PORT(bluetooth_zephyr_root_pointers);

    // Get connection handle from connection tracking list (not info.id which is identity ID)
    uint16_t conn_handle = mp_bt_zephyr_conn_to_handle(conn);
    if (conn_handle == 0xFFFF) {
        return BT_GATT_ITER_STOP;
    }

    if (data == NULL) {
        // Unsubscribe complete (remote end stopped, disconnect, or explicit unsubscribe)
        DEBUG_printf("gattc_notify_cb: unsubscribe complete conn_handle=%d changing=%d unsubscribing=%d\n",
                     conn_handle, rp->gattc_subscribe_changing, rp->gattc_unsubscribing);
        // Only fire WRITE_DONE if we explicitly requested unsubscribe via CCCD write.
        // Don't fire for disconnect-triggered cleanup (gattc_unsubscribing is false).
        // Don't fire if changing subscription types (gattc_subscribe_cb handles that).
        if (rp->gattc_unsubscribing && !rp->gattc_subscribe_changing) {
            rp->gattc_subscribe_active = false;
            rp->gattc_unsubscribing = false;
            // Fire WRITE_DONE for the CCCD write that triggered unsubscribe
            mp_bluetooth_gattc_on_read_write_status(MP_BLUETOOTH_IRQ_GATTC_WRITE_DONE,
                conn_handle, rp->gattc_subscribe_ccc_handle, 0);

            // Re-register auto-subscription for the value handle so that forced
            // gatts_notify() from the peripheral is still delivered. Zephyr requires
            // a registered subscription callback to deliver notifications; without
            // this, notifications after explicit unsubscribe are silently dropped.
            // Use NOTIFY property since auto-subscription prefers NOTIFY when available.
            gattc_register_auto_subscription(conn, conn_handle,
                rp->gattc_subscribe_value_handle, BT_GATT_CHRC_NOTIFY);
        }
        return BT_GATT_ITER_STOP;
    }

    uint8_t event = GATTC_NOTIFY_EVENT_TYPE(params);

    DEBUG_printf("gattc_notify_cb: %s received conn_handle=%d value_handle=0x%04x length=%d\n",
                 (event == MP_BLUETOOTH_IRQ_GATTC_INDICATE) ? "indication" : "notification",
                 conn_handle, params->value_handle, length);

    // Fire MicroPython callback with notification/indication data
    const uint8_t *data_ptr = (const uint8_t *)data;
    mp_bluetooth_gattc_on_data_available(event, conn_handle, params->value_handle,
                                         &data_ptr, &length, 1);

    return BT_GATT_ITER_CONTINUE;
}

// Auto-subscription notification callback - simpler than gattc_notify_cb
// Only delivers notifications to Python, no state management
static uint8_t gattc_auto_notify_cb(struct bt_conn *conn,
    struct bt_gatt_subscribe_params *params,
    const void *data, uint16_t length) {

    if (!mp_bluetooth_is_active()) {
        return BT_GATT_ITER_STOP;
    }

    if (data == NULL) {
        // Unsubscribe complete - just stop iteration, no state to update
        DEBUG_printf("gattc_auto_notify_cb: unsubscribe complete\n");
        return BT_GATT_ITER_STOP;
    }

    // Get connection handle from connection tracking list (not info.id which is identity ID)
    uint16_t conn_handle = mp_bt_zephyr_conn_to_handle(conn);
    if (conn_handle == 0xFFFF) {
        return BT_GATT_ITER_STOP;
    }

    uint8_t event = GATTC_NOTIFY_EVENT_TYPE(params);

    DEBUG_printf("gattc_auto_notify_cb: notification received conn_handle=%d value_handle=0x%04x length=%d\n",
                 conn_handle, params->value_handle, length);

    // Fire MicroPython callback with notification/indication data
    const uint8_t *data_ptr = (const uint8_t *)data;
    mp_bluetooth_gattc_on_data_available(event, conn_handle, params->value_handle,
                                         &data_ptr, &length, 1);

    return BT_GATT_ITER_CONTINUE;
}

// Helper to register a single auto-subscription for a specific subscription type
static bool gattc_register_auto_subscription_type(struct bt_conn *conn, uint16_t conn_handle,
    uint16_t value_handle, uint16_t sub_value, struct bt_conn_info *info) {
    mp_bluetooth_zephyr_root_pointers_t *rp = MP_STATE_PORT(bluetooth_zephyr_root_pointers);

    // Check if already registered for this handle+type on this connection
    for (int i = 0; i < GATTC_AUTO_SUBSCRIBE_MAX; i++) {
        if (rp->gattc_auto_subscriptions[i].in_use &&
            rp->gattc_auto_subscriptions[i].conn_handle == conn_handle &&
            rp->gattc_auto_subscriptions[i].params.value_handle == value_handle &&
            rp->gattc_auto_subscriptions[i].params.value == sub_value) {
            DEBUG_printf("gattc_register_auto_subscription: already registered handle=0x%04x type=0x%x\n",
                        value_handle, sub_value);
            return true;  // Already registered
        }
    }

    // Find a free slot
    int slot = -1;
    for (int i = 0; i < GATTC_AUTO_SUBSCRIBE_MAX; i++) {
        if (!rp->gattc_auto_subscriptions[i].in_use) {
            slot = i;
            break;
        }
    }

    if (slot < 0) {
        DEBUG_printf("gattc_register_auto_subscription: no free slots\n");
        return false;
    }

    // Set up subscription params
    struct bt_gatt_subscribe_params *params = &rp->gattc_auto_subscriptions[slot].params;
    memset(params, 0, sizeof(*params));
    params->notify = gattc_auto_notify_cb;
    params->value_handle = value_handle;
    // ccc_handle is required by bt_gatt_resubscribe assert but not actually used
    // Set to value_handle + 1 which is typically where CCCD is
    params->ccc_handle = value_handle + 1;
    params->value = sub_value;
    // Mark as volatile so Zephyr removes it on disconnect rather than preserving
    // it for bonded peers. We re-register during characteristic discovery on each
    // connection, so persistence isn't needed. Without VOLATILE, bonded peer
    // subscriptions survive disconnect and point to freed GC heap after soft reset.
    atomic_set_bit(params->flags, BT_GATT_SUBSCRIBE_FLAG_VOLATILE);

    // Register with Zephyr - this adds to internal subscription list without CCCD write
    int err = bt_gatt_resubscribe(info->id, info->le.dst, params);
    if (err && err != -EALREADY) {
        DEBUG_printf("gattc_register_auto_subscription: bt_gatt_resubscribe failed err=%d\n", err);
        return false;
    }

    // Mark slot as in use
    rp->gattc_auto_subscriptions[slot].conn_handle = conn_handle;
    rp->gattc_auto_subscriptions[slot].in_use = true;

    DEBUG_printf("gattc_register_auto_subscription: registered slot=%d handle=0x%04x type=0x%x\n",
                slot, value_handle, sub_value);
    return true;
}

// Register auto-subscription for a characteristic handle.
// This allows notifications to be delivered without explicit CCCD write,
// matching NimBLE's behavior. Called during characteristic discovery for
// characteristics with NOTIFY or INDICATE properties.
//
// When BT_GATT_SUBSCRIBE_HAS_RECEIVED_OPCODE is defined, the stack sets
// params->received_opcode before invoking the callback, so notification
// type is detected from the actual ATT opcode regardless of subscription.
// Without it, we subscribe to only ONE type (prefer NOTIFY if supported)
// so params->value unambiguously indicates the event type.
static void gattc_register_auto_subscription(struct bt_conn *conn, uint16_t conn_handle, uint16_t value_handle, uint8_t properties) {
    mp_bluetooth_zephyr_root_pointers_t *rp = MP_STATE_PORT(bluetooth_zephyr_root_pointers);
    if (!rp) {
        return;
    }

    // Check if notify or indicate is supported
    if (!(properties & (BT_GATT_CHRC_NOTIFY | BT_GATT_CHRC_INDICATE))) {
        return;
    }

    // Get peer address for bt_gatt_resubscribe
    struct bt_conn_info info;
    if (bt_conn_get_info(conn, &info) != 0) {
        DEBUG_printf("gattc_register_auto_subscription: failed to get conn info\n");
        return;
    }

    // Subscribe to one type. With BT_GATT_SUBSCRIBE_HAS_RECEIVED_OPCODE the
    // stack provides the actual opcode, but subscribing to one type is still
    // correct behavior. Prefer NOTIFY if supported (more common, lighter weight).
    uint16_t sub_value;
    if (properties & BT_GATT_CHRC_NOTIFY) {
        sub_value = BT_GATT_CCC_NOTIFY;
    } else {
        sub_value = BT_GATT_CCC_INDICATE;
    }
    gattc_register_auto_subscription_type(conn, conn_handle, value_handle, sub_value, &info);
}

// Clear auto-subscriptions for a disconnected connection
static void gattc_clear_auto_subscriptions(uint16_t conn_handle) {
    mp_bluetooth_zephyr_root_pointers_t *rp = MP_STATE_PORT(bluetooth_zephyr_root_pointers);
    if (!rp) {
        return;
    }

    for (int i = 0; i < GATTC_AUTO_SUBSCRIBE_MAX; i++) {
        if (rp->gattc_auto_subscriptions[i].in_use &&
            rp->gattc_auto_subscriptions[i].conn_handle == conn_handle) {
            rp->gattc_auto_subscriptions[i].in_use = false;
            DEBUG_printf("gattc_clear_auto_subscriptions: cleared slot=%d\n", i);
        }
    }
}

// Remove auto-subscription for a specific value handle when explicit subscription is made.
// This prevents duplicate callbacks when both auto and explicit subscriptions exist.
static void gattc_remove_auto_subscription_for_handle(struct bt_conn *conn, uint16_t conn_handle, uint16_t value_handle) {
    mp_bluetooth_zephyr_root_pointers_t *rp = MP_STATE_PORT(bluetooth_zephyr_root_pointers);
    if (!rp) {
        return;
    }

    for (int i = 0; i < GATTC_AUTO_SUBSCRIBE_MAX; i++) {
        if (rp->gattc_auto_subscriptions[i].in_use &&
            rp->gattc_auto_subscriptions[i].conn_handle == conn_handle &&
            rp->gattc_auto_subscriptions[i].params.value_handle == value_handle) {
            // Unsubscribe from Zephyr's internal list
            bt_gatt_unsubscribe(conn, &rp->gattc_auto_subscriptions[i].params);
            rp->gattc_auto_subscriptions[i].in_use = false;
            DEBUG_printf("gattc_remove_auto_subscription_for_handle: removed slot=%d handle=0x%04x\n", i, value_handle);
        }
    }
}

static uint8_t gattc_descriptor_discover_cb(struct bt_conn *conn,
    const struct bt_gatt_attr *attr, struct bt_gatt_discover_params *params) {

    if (!mp_bluetooth_is_active()) {
        return BT_GATT_ITER_STOP;
    }

    mp_bluetooth_zephyr_root_pointers_t *rp = MP_STATE_PORT(bluetooth_zephyr_root_pointers);
    uint16_t conn_handle = rp->gattc_discover_conn_handle;

    if (attr == NULL) {
        // Discovery complete
        mp_bluetooth_gattc_on_discover_complete(MP_BLUETOOTH_IRQ_GATTC_DESCRIPTOR_DONE, conn_handle, 0);
        return BT_GATT_ITER_STOP;
    }

    // Check if this is a CCCD (Client Characteristic Configuration Descriptor, UUID 0x2902)
    if (attr->uuid->type == BT_UUID_TYPE_16 && BT_UUID_16(attr->uuid)->val == 0x2902) {
        // Store CCCD info for later use when Python writes to enable notifications
        // Actual subscription happens in mp_bluetooth_gattc_write() when CCCD is written
        DEBUG_printf("Found CCCD: handle=0x%04x, char_value_handle=0x%04x\n",
                     attr->handle, rp->gattc_discover_char_value_handle);
        rp->gattc_subscribe_ccc_handle = attr->handle;
        rp->gattc_subscribe_value_handle = rp->gattc_discover_char_value_handle;
        rp->gattc_subscribe_conn_handle = conn_handle;
        rp->gattc_subscribe_active = false;
        rp->gattc_subscribe_changing = false;
        rp->gattc_unsubscribing = false;
        rp->gattc_subscribe_pending = false;
    }

    // Report descriptor
    mp_obj_bluetooth_uuid_t desc_uuid = zephyr_uuid_to_mp(attr->uuid);
    mp_bluetooth_gattc_on_descriptor_result(conn_handle, attr->handle, &desc_uuid);

    return BT_GATT_ITER_CONTINUE;
}

// Read callback
static uint8_t gattc_read_cb(struct bt_conn *conn, uint8_t err,
    struct bt_gatt_read_params *params, const void *data, uint16_t length) {

    struct bt_conn_info conn_info;
    int state = -1;
    if (conn && bt_conn_get_info(conn, &conn_info) == 0) {
        state = conn_info.state;
    }
    DEBUG_printf("gattc_read_cb: err=%d data=%p length=%d conn=%p conn_state=%d\n",
        err, data, length, conn, state);

    if (!mp_bluetooth_is_active()) {
        return BT_GATT_ITER_STOP;
    }

    mp_bluetooth_zephyr_root_pointers_t *rp = MP_STATE_PORT(bluetooth_zephyr_root_pointers);
    uint16_t conn_handle = rp->gattc_read_conn_handle;
    uint16_t value_handle = rp->gattc_read_value_handle;

    DEBUG_printf("gattc_read_cb: conn_handle=%d value_handle=0x%04x data=%p len=%d err=%d\n",
        conn_handle, value_handle, data, length, err);

    if (data != NULL) {
        // Data available (may be empty, length=0 for empty characteristics)
        rp->gattc_read_data_received = true;
        const uint8_t *data_ptr = (const uint8_t *)data;
        mp_bluetooth_gattc_on_data_available(MP_BLUETOOTH_IRQ_GATTC_READ_RESULT,
            conn_handle, value_handle, &data_ptr, &length, 1);
        return BT_GATT_ITER_CONTINUE;
    }

    // Read complete (data == NULL).
    // Zephyr skips the data callback for empty characteristics and calls with data=NULL directly.
    // Only fire empty READ_RESULT if no data was received and the read was successful.
    if (err == 0 && !rp->gattc_read_data_received) {
        const uint8_t *empty_ptr = (const uint8_t *)"";
        uint16_t empty_len = 0;
        mp_bluetooth_gattc_on_data_available(MP_BLUETOOTH_IRQ_GATTC_READ_RESULT,
            conn_handle, value_handle, &empty_ptr, &empty_len, 1);
    }

    // Fire READ_DONE to signal completion (with error status if applicable)
    mp_bluetooth_gattc_on_read_write_status(MP_BLUETOOTH_IRQ_GATTC_READ_DONE,
        conn_handle, value_handle, err);
    return BT_GATT_ITER_STOP;
}

// Write callback
static void gattc_write_cb(struct bt_conn *conn, uint8_t err,
    struct bt_gatt_write_params *params) {

    if (!mp_bluetooth_is_active()) {
        return;
    }

    mp_bluetooth_zephyr_root_pointers_t *rp = MP_STATE_PORT(bluetooth_zephyr_root_pointers);
    mp_bluetooth_gattc_on_read_write_status(MP_BLUETOOTH_IRQ_GATTC_WRITE_DONE,
        rp->gattc_write_conn_handle, rp->gattc_write_value_handle, err);
}

// MTU exchange callback (for bt_gatt_exchange_mtu completion)
// This callback is called when bt_gatt_exchange_mtu() completes.
// The actual MTU notification is handled by bt_gatt_cb.att_mtu_updated which fires
// when the MTU changes, so we only log here.
static void gattc_mtu_exchange_cb(struct bt_conn *conn, uint8_t err,
    struct bt_gatt_exchange_params *params) {
    (void)params;
    (void)conn;

    // Log the completion status
    DEBUG_printf("GATTC MTU exchange complete: err=%d\n", err);

    // Note: We don't notify Python here because bt_gatt_cb.att_mtu_updated
    // handles MTU notifications. This callback just indicates the exchange
    // operation completed (successfully or not).
}

#endif // MICROPY_PY_BLUETOOTH_ENABLE_GATT_CLIENT

int mp_bluetooth_gattc_discover_primary_services(uint16_t conn_handle, const mp_obj_bluetooth_uuid_t *uuid) {
    #if MICROPY_PY_BLUETOOTH_ENABLE_GATT_CLIENT
    if (!mp_bluetooth_is_active()) {
        return ERRNO_BLUETOOTH_NOT_ACTIVE;
    }

    struct bt_conn *conn = mp_bt_zephyr_get_conn(conn_handle);
    if (conn == NULL) {
        return MP_ENOTCONN;
    }

    mp_bluetooth_zephyr_root_pointers_t *rp = MP_STATE_PORT(bluetooth_zephyr_root_pointers);

    // Set up discovery params
    memset(&rp->gattc_discover_params, 0, sizeof(rp->gattc_discover_params));
    rp->gattc_discover_params.func = gattc_service_discover_cb;
    rp->gattc_discover_params.start_handle = BT_ATT_FIRST_ATTRIBUTE_HANDLE;
    rp->gattc_discover_params.end_handle = BT_ATT_LAST_ATTRIBUTE_HANDLE;
    rp->gattc_discover_params.type = BT_GATT_DISCOVER_PRIMARY;

    if (uuid != NULL) {
        rp->gattc_discover_params.uuid = create_zephyr_uuid(uuid);
    } else {
        rp->gattc_discover_params.uuid = NULL;
    }

    rp->gattc_discover_conn_handle = conn_handle;

    int err = bt_gatt_discover(conn, &rp->gattc_discover_params);

    // Process work queue to send ATT request (Issue #14 fix)
    if (err == 0) {
        mp_bluetooth_zephyr_work_process();
    }

    return bt_err_to_errno(err);
    #else
    (void)conn_handle;
    (void)uuid;
    return MP_EOPNOTSUPP;
    #endif
}

int mp_bluetooth_gattc_discover_characteristics(uint16_t conn_handle, uint16_t start_handle, uint16_t end_handle, const mp_obj_bluetooth_uuid_t *uuid) {
    #if MICROPY_PY_BLUETOOTH_ENABLE_GATT_CLIENT
    if (!mp_bluetooth_is_active()) {
        return ERRNO_BLUETOOTH_NOT_ACTIVE;
    }

    struct bt_conn *conn = mp_bt_zephyr_get_conn(conn_handle);
    if (conn == NULL) {
        return MP_ENOTCONN;
    }

    mp_bluetooth_zephyr_root_pointers_t *rp = MP_STATE_PORT(bluetooth_zephyr_root_pointers);

    // Clear any pending characteristic from previous discovery
    rp->gattc_pending_char.pending = false;

    // Set up discovery params
    memset(&rp->gattc_discover_params, 0, sizeof(rp->gattc_discover_params));
    rp->gattc_discover_params.func = gattc_characteristic_discover_cb;
    rp->gattc_discover_params.start_handle = start_handle;
    rp->gattc_discover_params.end_handle = end_handle;
    rp->gattc_discover_params.type = BT_GATT_DISCOVER_CHARACTERISTIC;

    // Note: Zephyr doesn't support UUID filtering directly for characteristics,
    // so we discover all and could filter in callback if needed.
    // For now, we don't filter (uuid parameter is ignored).
    rp->gattc_discover_params.uuid = NULL;
    (void)uuid;

    rp->gattc_discover_conn_handle = conn_handle;
    rp->gattc_discover_end_handle = end_handle;

    int err = bt_gatt_discover(conn, &rp->gattc_discover_params);

    // Process work queue to send ATT request (Issue #14 fix)
    if (err == 0) {
        mp_bluetooth_zephyr_work_process();
    }

    return bt_err_to_errno(err);
    #else
    (void)conn_handle;
    (void)start_handle;
    (void)end_handle;
    (void)uuid;
    return MP_EOPNOTSUPP;
    #endif
}

int mp_bluetooth_gattc_discover_descriptors(uint16_t conn_handle, uint16_t start_handle, uint16_t end_handle) {
    #if MICROPY_PY_BLUETOOTH_ENABLE_GATT_CLIENT
    if (!mp_bluetooth_is_active()) {
        return ERRNO_BLUETOOTH_NOT_ACTIVE;
    }

    struct bt_conn *conn = mp_bt_zephyr_get_conn(conn_handle);
    if (conn == NULL) {
        return MP_ENOTCONN;
    }

    mp_bluetooth_zephyr_root_pointers_t *rp = MP_STATE_PORT(bluetooth_zephyr_root_pointers);

    // Set up discovery params
    memset(&rp->gattc_discover_params, 0, sizeof(rp->gattc_discover_params));
    rp->gattc_discover_params.func = gattc_descriptor_discover_cb;
    rp->gattc_discover_params.start_handle = start_handle;
    rp->gattc_discover_params.end_handle = end_handle;
    rp->gattc_discover_params.type = BT_GATT_DISCOVER_DESCRIPTOR;
    rp->gattc_discover_params.uuid = NULL;

    rp->gattc_discover_conn_handle = conn_handle;
    // Track characteristic value handle for potential CCCD subscription
    // start_handle IS the characteristic value handle (per Python API)
    rp->gattc_discover_char_value_handle = start_handle;

    int err = bt_gatt_discover(conn, &rp->gattc_discover_params);

    // FIX Issue #14: Process work queue immediately to send ATT request.
    // On WB55 without FreeRTOS, work items aren't processed until next poll cycle,
    // which can cause 4-5 second delays waiting for ATT timeout.
    // Processing the work queue here ensures the request is sent immediately.
    if (err == 0) {
        mp_bluetooth_zephyr_work_process();
    }

    return bt_err_to_errno(err);
    #else
    (void)conn_handle;
    (void)start_handle;
    (void)end_handle;
    return MP_EOPNOTSUPP;
    #endif
}

int mp_bluetooth_gattc_read(uint16_t conn_handle, uint16_t value_handle) {
    #if MICROPY_PY_BLUETOOTH_ENABLE_GATT_CLIENT
    if (!mp_bluetooth_is_active()) {
        return ERRNO_BLUETOOTH_NOT_ACTIVE;
    }

    struct bt_conn *conn = mp_bt_zephyr_get_conn(conn_handle);
    if (conn == NULL) {
        return MP_ENOTCONN;
    }

    mp_bluetooth_zephyr_root_pointers_t *rp = MP_STATE_PORT(bluetooth_zephyr_root_pointers);

    // Set up read params
    memset(&rp->gattc_read_params, 0, sizeof(rp->gattc_read_params));
    rp->gattc_read_params.func = gattc_read_cb;
    rp->gattc_read_params.handle_count = 1;
    rp->gattc_read_params.single.handle = value_handle;
    rp->gattc_read_params.single.offset = 0;

    rp->gattc_read_conn_handle = conn_handle;
    rp->gattc_read_value_handle = value_handle;
    rp->gattc_read_data_received = false;  // Reset for new read operation

    #if ZEPHYR_BLE_DEBUG
    struct bt_conn_info conn_info;
    bt_conn_get_info(conn, &conn_info);
    DEBUG_printf("gattc_read: conn_handle=%d value_handle=0x%04x conn_state=%d\n",
        conn_handle, value_handle, conn_info.state);
    #endif

    int err = bt_gatt_read(conn, &rp->gattc_read_params);

    #if ZEPHYR_BLE_DEBUG
    bt_conn_get_info(conn, &conn_info);
    DEBUG_printf("gattc_read: bt_gatt_read returned %d (conn_state=%d)\n", err, conn_info.state);
    #endif

    // Process work queue immediately to send ATT request (same fix as Issue #14)
    if (err == 0) {
        mp_bluetooth_zephyr_work_process();
    }

    return bt_err_to_errno(err);
    #else
    (void)conn_handle;
    (void)value_handle;
    return MP_EOPNOTSUPP;
    #endif
}

int mp_bluetooth_gattc_write(uint16_t conn_handle, uint16_t value_handle, const uint8_t *value, size_t value_len, unsigned int mode) {
    #if MICROPY_PY_BLUETOOTH_ENABLE_GATT_CLIENT
    if (!mp_bluetooth_is_active()) {
        return ERRNO_BLUETOOTH_NOT_ACTIVE;
    }

    struct bt_conn *conn = mp_bt_zephyr_get_conn(conn_handle);
    if (conn == NULL) {
        return MP_ENOTCONN;
    }

    mp_bluetooth_zephyr_root_pointers_t *rp = MP_STATE_PORT(bluetooth_zephyr_root_pointers);
    int err;

    // Check if this is a CCCD write (for enabling/disabling notifications)
    if (value_len == 2 && value_handle == rp->gattc_subscribe_ccc_handle) {
        uint16_t cccd_value = value[0] | (value[1] << 8);

        DEBUG_printf("CCCD write: handle=0x%04x value=0x%04x active=%d\n",
                     value_handle, cccd_value, rp->gattc_subscribe_active);

        if (cccd_value == 0x0000) {
            // Unsubscribe - disable notifications/indications
            if (rp->gattc_subscribe_active) {
                DEBUG_printf("CCCD write: unsubscribing\n");
                // Set flag so gattc_notify_cb knows to fire WRITE_DONE
                rp->gattc_unsubscribing = true;
                err = bt_gatt_unsubscribe(conn, &rp->gattc_subscribe_params);
                // gattc_notify_cb fires with data=NULL when complete
                if (err == 0) {
                    // Process work queue to send ATT request
                    mp_bluetooth_zephyr_work_process();
                    return 0;
                }
                // Unsubscribe failed, clear flag
                rp->gattc_unsubscribing = false;
                DEBUG_printf("CCCD write: unsubscribe failed err=%d\n", err);
                return bt_err_to_errno(err);
            }
            // Not currently subscribed, nothing to do - return success
            // (Python expects WRITE_DONE callback, fire it manually)
            mp_bluetooth_gattc_on_read_write_status(MP_BLUETOOTH_IRQ_GATTC_WRITE_DONE,
                conn_handle, value_handle, 0);
            return 0;
        } else {
            // Subscribe - enable notifications (0x0001) or indications (0x0002)
            DEBUG_printf("CCCD write: subscribing with value=0x%04x\n", cccd_value);

            // Unsubscribe first if already active (to change subscription type)
            if (rp->gattc_subscribe_active) {
                // Set flag to indicate we're intentionally changing subscriptions
                // This prevents the old unsubscribe callback from interfering
                rp->gattc_subscribe_changing = true;
                bt_gatt_unsubscribe(conn, &rp->gattc_subscribe_params);
                rp->gattc_subscribe_active = false;
            }

            // Remove any auto-subscription for this handle to prevent duplicate callbacks
            gattc_remove_auto_subscription_for_handle(conn, conn_handle, rp->gattc_subscribe_value_handle);

            // Set up subscription parameters
            memset(&rp->gattc_subscribe_params, 0, sizeof(rp->gattc_subscribe_params));
            rp->gattc_subscribe_params.notify = gattc_notify_cb;
            rp->gattc_subscribe_params.subscribe = gattc_subscribe_cb;
            rp->gattc_subscribe_params.value_handle = rp->gattc_subscribe_value_handle;
            rp->gattc_subscribe_params.ccc_handle = value_handle;
            rp->gattc_subscribe_params.value = cccd_value;
            atomic_set_bit(rp->gattc_subscribe_params.flags, BT_GATT_SUBSCRIBE_FLAG_VOLATILE);

            rp->gattc_subscribe_conn_handle = conn_handle;
            rp->gattc_subscribe_pending = true;

            err = bt_gatt_subscribe(conn, &rp->gattc_subscribe_params);
            if (err == 0) {
                // Process work queue to send ATT request
                mp_bluetooth_zephyr_work_process();
                // gattc_subscribe_cb will fire and set active flag + WRITE_DONE
                return 0;
            } else if (err == -EALREADY) {
                // Already subscribed, treat as success
                rp->gattc_subscribe_pending = false;
                rp->gattc_subscribe_active = true;
                mp_bluetooth_gattc_on_read_write_status(MP_BLUETOOTH_IRQ_GATTC_WRITE_DONE,
                    conn_handle, value_handle, 0);
                return 0;
            }
            rp->gattc_subscribe_pending = false;
            DEBUG_printf("CCCD write: subscribe failed err=%d\n", err);
            return bt_err_to_errno(err);
        }
    }

    // Normal write (not a CCCD)
    if (mode == MP_BLUETOOTH_WRITE_MODE_NO_RESPONSE) {
        // Write without response
        err = bt_gatt_write_without_response(conn, value_handle, value, value_len, false);
    } else {
        // Write with response
        memset(&rp->gattc_write_params, 0, sizeof(rp->gattc_write_params));
        rp->gattc_write_params.func = gattc_write_cb;
        rp->gattc_write_params.handle = value_handle;
        rp->gattc_write_params.data = value;
        rp->gattc_write_params.length = value_len;

        rp->gattc_write_conn_handle = conn_handle;
        rp->gattc_write_value_handle = value_handle;

        err = bt_gatt_write(conn, &rp->gattc_write_params);
    }

    // Process work queue to send ATT request
    if (err == 0) {
        mp_bluetooth_zephyr_work_process();
    }

    return bt_err_to_errno(err);
    #else
    (void)conn_handle;
    (void)value_handle;
    (void)value;
    (void)value_len;
    (void)mode;
    return MP_EOPNOTSUPP;
    #endif
}

int mp_bluetooth_gattc_exchange_mtu(uint16_t conn_handle) {
    #if MICROPY_PY_BLUETOOTH_ENABLE_GATT_CLIENT
    if (!mp_bluetooth_is_active()) {
        return ERRNO_BLUETOOTH_NOT_ACTIVE;
    }

    struct bt_conn *conn = mp_bt_zephyr_get_conn(conn_handle);
    if (conn == NULL) {
        return MP_ENOTCONN;
    }

    mp_bluetooth_zephyr_root_pointers_t *rp = MP_STATE_PORT(bluetooth_zephyr_root_pointers);

    memset(&rp->gattc_mtu_params, 0, sizeof(rp->gattc_mtu_params));
    rp->gattc_mtu_params.func = gattc_mtu_exchange_cb;
    rp->gattc_mtu_conn_handle = conn_handle;

    int err = bt_gatt_exchange_mtu(conn, &rp->gattc_mtu_params);

    // Process work queue immediately to send ATT request
    if (err == 0) {
        mp_bluetooth_zephyr_work_process();
    }

    return bt_err_to_errno(err);
    #else
    (void)conn_handle;
    return MP_EOPNOTSUPP;
    #endif
}

// ============================================================================
// Pairing/Bonding Implementation (Phase 1: Basic pairing without persistent storage)
// ============================================================================

// Authentication callback handlers that translate Zephyr auth events to MicroPython IRQ events

static void zephyr_passkey_display_cb(struct bt_conn *conn, unsigned int passkey) {
    DEBUG_printf("zephyr_passkey_display_cb: passkey=%06u\n", passkey);

    uint16_t conn_handle = mp_bt_zephyr_auth_get_conn_handle(conn);
    if (conn_handle == 0xFF) {
        return;
    }

    // Store auth state for tracking
    mp_bluetooth_zephyr_root_pointers_t *rp = MP_STATE_PORT(bluetooth_zephyr_root_pointers);
    if (rp) {
        rp->auth_conn_handle = conn_handle;
        rp->auth_action = MP_BLUETOOTH_PASSKEY_ACTION_DISPLAY;
        rp->auth_passkey = passkey;
    }

    // Fire _IRQ_PASSKEY_ACTION event: user should display this passkey
    mp_bluetooth_gap_on_passkey_action(conn_handle, MP_BLUETOOTH_PASSKEY_ACTION_DISPLAY, passkey);
}

static void zephyr_passkey_entry_cb(struct bt_conn *conn) {
    DEBUG_printf("zephyr_passkey_entry_cb\n");

    uint16_t conn_handle = mp_bt_zephyr_auth_get_conn_handle(conn);
    if (conn_handle == 0xFF) {
        return;
    }

    // Store auth state
    mp_bluetooth_zephyr_root_pointers_t *rp = MP_STATE_PORT(bluetooth_zephyr_root_pointers);
    if (rp) {
        rp->auth_conn_handle = conn_handle;
        rp->auth_action = MP_BLUETOOTH_PASSKEY_ACTION_INPUT;
        rp->auth_passkey = 0;
    }

    // Fire _IRQ_PASSKEY_ACTION event: user should enter passkey
    mp_bluetooth_gap_on_passkey_action(conn_handle, MP_BLUETOOTH_PASSKEY_ACTION_INPUT, 0);
}

static void zephyr_passkey_confirm_cb(struct bt_conn *conn, unsigned int passkey) {
    DEBUG_printf("zephyr_passkey_confirm_cb: passkey=%06u\n", passkey);

    uint16_t conn_handle = mp_bt_zephyr_auth_get_conn_handle(conn);
    if (conn_handle == 0xFF) {
        return;
    }

    // Store auth state
    mp_bluetooth_zephyr_root_pointers_t *rp = MP_STATE_PORT(bluetooth_zephyr_root_pointers);
    if (rp) {
        rp->auth_conn_handle = conn_handle;
        rp->auth_action = MP_BLUETOOTH_PASSKEY_ACTION_NUMERIC_COMPARISON;
        rp->auth_passkey = passkey;
    }

    // Fire _IRQ_PASSKEY_ACTION event: user should confirm passkey matches
    mp_bluetooth_gap_on_passkey_action(conn_handle, MP_BLUETOOTH_PASSKEY_ACTION_NUMERIC_COMPARISON, passkey);
}

static void zephyr_pairing_confirm_cb(struct bt_conn *conn) {
    DEBUG_printf("zephyr_pairing_confirm_cb\n");
    uint16_t conn_handle = mp_bt_zephyr_auth_get_conn_handle(conn);
    if (conn_handle == 0xFF) {
        DEBUG_printf("  ERROR: Connection not found!\n");
        return;
    }

    // Mark pairing in progress - security_changed will defer encryption callback
    mp_bluetooth_zephyr_root_pointers_t *rp = MP_STATE_PORT(bluetooth_zephyr_root_pointers);
    if (rp) {
        rp->pairing_in_progress = true;
    }

    // For Just Works pairing, auto-confirm without firing _IRQ_PASSKEY_ACTION.
    // This matches NimBLE behavior where Just Works is auto-accepted internally
    // and applications don't need to handle the passkey action event.
    // Applications that need to reject Just Works pairing can set a different IO capability.
    int err = bt_conn_auth_pairing_confirm(conn);
    DEBUG_printf("  bt_conn_auth_pairing_confirm: %d\n", err);
}

static void zephyr_auth_cancel_cb(struct bt_conn *conn) {
    DEBUG_printf("zephyr_auth_cancel_cb\n");

    uint16_t conn_handle = mp_bt_zephyr_auth_get_conn_handle(conn);
    if (conn_handle == 0xFF) {
        return;
    }

    // Clear auth state
    mp_bluetooth_zephyr_root_pointers_t *rp = MP_STATE_PORT(bluetooth_zephyr_root_pointers);
    if (rp) {
        rp->auth_conn_handle = 0;
        rp->auth_action = 0;
        rp->auth_passkey = 0;
    }

    DEBUG_printf("  Authentication cancelled for conn_handle=%d\n", conn_handle);
}

static void zephyr_pairing_complete_cb(struct bt_conn *conn, bool bonded) {
    DEBUG_printf("zephyr_pairing_complete_cb: bonded=%d\n", bonded);

    mp_bluetooth_zephyr_root_pointers_t *rp = MP_STATE_PORT(bluetooth_zephyr_root_pointers);
    if (!rp) {
        return;
    }

    // Clear auth state
    rp->auth_conn_handle = 0;
    rp->auth_action = 0;
    rp->auth_passkey = 0;

    // Store bonded flag from pairing result
    rp->pairing_complete_received = true;
    rp->pending_pairing_bonded = bonded;

    // Check if security_changed already arrived (it fires first on native Zephyr)
    if (rp->pending_security_update) {
        DEBUG_printf("Both pairing_complete and security_changed received, firing callback\n");
        rp->pairing_in_progress = false;
        rp->pending_security_update = false;
        rp->pairing_complete_received = false;
        mp_bluetooth_gatts_on_encryption_update(
            rp->pending_sec_conn,
            rp->pending_sec_encrypted,
            rp->pending_sec_authenticated,
            bonded,
            rp->pending_sec_key_size);
    }
    // Otherwise, security_changed hasn't fired yet (HAL builds).
    // Keep pairing_in_progress=true so security_changed will pick up the bonded flag.
}

static void zephyr_pairing_failed_cb(struct bt_conn *conn, enum bt_security_err reason) {
    DEBUG_printf("zephyr_pairing_failed_cb: reason=%d\n", reason);

    uint16_t conn_handle = mp_bt_zephyr_auth_get_conn_handle(conn);
    if (conn_handle == 0xFF) {
        DEBUG_printf("  ERROR: Connection not found!\n");
        return;
    }

    // Clear pairing state
    mp_bluetooth_zephyr_root_pointers_t *rp = MP_STATE_PORT(bluetooth_zephyr_root_pointers);
    if (rp) {
        rp->pairing_in_progress = false;
        rp->pending_security_update = false;
        rp->pairing_complete_received = false;
        rp->auth_conn_handle = 0;
        rp->auth_action = 0;
        rp->auth_passkey = 0;
    }

    // Fire _IRQ_ENCRYPTION_UPDATE with encrypted=false to indicate failure
    mp_bluetooth_gatts_on_encryption_update(conn_handle, false, false, false, 0);
}

// Security configuration flags
static bool mp_bt_zephyr_mitm_protection = false;  // Default: No MITM (Just Works)
static bool mp_bt_zephyr_le_secure = false;       // Default: Allow legacy pairing
static bool mp_bt_zephyr_bonding = true;          // Default: Bonding enabled (CONFIG_BT_BONDABLE=1)

// IO capability setting (default: NO_INPUT_NO_OUTPUT for Just Works)
// Note: This is NOT a Zephyr enum - it's the value used by mp_bluetooth_set_io_capability()
// 0 = NO_INPUT_NO_OUTPUT (Just Works), 1 = DISPLAY_ONLY, 2 = KEYBOARD_ONLY, etc.
static uint8_t mp_bt_zephyr_io_capability = 0;  // Default: Just Works

// Authentication callback structures (forward declared earlier for use in mp_bluetooth_init)
// These are dynamically configured based on IO capability to control pairing method
struct bt_conn_auth_cb mp_bt_zephyr_auth_callbacks = {
    // Initially NULL - configured by first call to mp_bluetooth_set_io_capability()
    // or by default in mp_bluetooth_init() for Just Works (NO_INPUT_NO_OUTPUT)
    .passkey_display = NULL,
    .passkey_entry = NULL,
    .passkey_confirm = NULL,
    .pairing_confirm = NULL,  // Set to zephyr_pairing_confirm_cb for Just Works
    .cancel = NULL,           // Set to zephyr_auth_cancel_cb when pairing_confirm is set
};

struct bt_conn_auth_info_cb mp_bt_zephyr_auth_info_callbacks = {
    .pairing_complete = zephyr_pairing_complete_cb,
    .pairing_failed = zephyr_pairing_failed_cb,
};

// ============================================================================
// Pairing/Bonding API Implementation
// ============================================================================

int mp_bluetooth_gap_pair(uint16_t conn_handle) {
    DEBUG_printf("mp_bluetooth_gap_pair: conn_handle=%d mitm=%d le_secure=%d bonding=%d io_cap=%d\n",
                 conn_handle, mp_bt_zephyr_mitm_protection, mp_bt_zephyr_le_secure,
                 mp_bt_zephyr_bonding, mp_bt_zephyr_io_capability);

    if (!mp_bluetooth_is_active()) {
        return ERRNO_BLUETOOTH_NOT_ACTIVE;
    }

    struct bt_conn *conn = mp_bt_zephyr_get_conn(conn_handle);
    if (conn == NULL) {
        return MP_ENOTCONN;
    }

    // Determine if MITM protection is actually achievable based on IO capability.
    // IO capability 0 (NO_INPUT_NO_OUTPUT) cannot provide MITM protection.
    // When MITM is requested but not achievable, we downgrade to L2 (Just Works)
    // to match NimBLE behavior which also downgrades in this case.
    bool mitm_possible = (mp_bt_zephyr_io_capability != 0);
    bool request_mitm = mp_bt_zephyr_mitm_protection && mitm_possible;

    // Choose security level based on config flags and achievable MITM:
    // - le_secure=true, mitm achievable: BT_SECURITY_L4 (SC required + MITM)
    // - le_secure=false, mitm achievable: BT_SECURITY_L3 (MITM required, legacy or SC)
    // - mitm not achievable/requested: BT_SECURITY_L2 (Just Works, legacy or SC)
    //
    // Note: Zephyr doesn't have a security level for "SC without MITM". With L2,
    // SC will be used if both devices support it. The le_secure flag indicates
    // preference for SC, but can't be strictly enforced without MITM.
    bt_security_t sec_level;

    if (mp_bt_zephyr_le_secure && request_mitm) {
        sec_level = BT_SECURITY_L4;  // SC required + MITM
        DEBUG_printf("  Requesting BT_SECURITY_L4 (SC + MITM)\n");
    } else if (request_mitm) {
        sec_level = BT_SECURITY_L3;  // MITM required (legacy or SC)
        DEBUG_printf("  Requesting BT_SECURITY_L3 (MITM)\n");
    } else {
        sec_level = BT_SECURITY_L2;  // Just Works (legacy or SC)
        DEBUG_printf("  Requesting BT_SECURITY_L2 (Just Works)\n");
        if (mp_bt_zephyr_mitm_protection && !mitm_possible) {
            DEBUG_printf("  Note: MITM requested but IO capability is NO_INPUT_NO_OUTPUT, using Just Works\n");
        }
    }

    // Mark pairing in progress before starting SMP. This ensures security_changed
    // callback defers until pairing_complete provides the bonded flag.
    // On the central side, Zephyr's SMP doesn't call pairing_confirm for SC Just Works
    // when the central initiates (SMP_FLAG_SEC_REQ is not set), so pairing_confirm_cb
    // won't set this flag. Setting it here covers all pairing initiation paths.
    mp_bluetooth_zephyr_root_pointers_t *rp = MP_STATE_PORT(bluetooth_zephyr_root_pointers);
    if (rp) {
        rp->pairing_in_progress = true;
        rp->pending_security_update = false;
        rp->pairing_complete_received = false;
    }

    int err = bt_conn_set_security(conn, sec_level);
    DEBUG_printf("  bt_conn_set_security returned %d\n", err);

    if (err && rp) {
        // bt_conn_set_security failed, clear pairing state
        rp->pairing_in_progress = false;
    }

    return bt_err_to_errno(err);
}

int mp_bluetooth_gap_unpair(uint8_t addr_type, const uint8_t *addr) {
    DEBUG_printf("mp_bluetooth_gap_unpair: addr=%p\n", addr);

    bt_addr_le_t le_addr;
    if (addr != NULL) {
        le_addr.type = addr_type;
        memcpy(le_addr.a.val, addr, 6);
    }

    #if MICROPY_PY_BLUETOOTH_ENABLE_PAIRING_BONDING && !defined(__ZEPHYR__)
    // Delete stored bond keys from Python secret storage.
    // With CONFIG_BT_SETTINGS=0, Zephyr's bt_keys_clear() skips the IS_ENABLED
    // delete path, so we handle deletion at the MicroPython API level instead.
    if (addr == NULL) {
        // Delete all: always request index 0 since each deletion shifts entries down.
        // Copy addr to stack local before calling set_secret to avoid GC collecting
        // the Python bytes object backing the get_secret return value.
        const uint8_t *value;
        size_t value_len;
        bt_addr_le_t del_addr;
        for (uint8_t i = 0; i < CONFIG_BT_MAX_PAIRED; i++) {
            if (!mp_bluetooth_gap_on_get_secret(
                    MP_BLUETOOTH_ZEPHYR_SECRET_KEYS, 0,
                    NULL, 0, &value, &value_len)) {
                break;
            }
            if (value_len >= sizeof(bt_addr_le_t)) {
                memcpy(&del_addr, value, sizeof(bt_addr_le_t));
                mp_bluetooth_gap_on_set_secret(
                    MP_BLUETOOTH_ZEPHYR_SECRET_KEYS,
                    (const uint8_t *)&del_addr, sizeof(bt_addr_le_t),
                    NULL, 0);
            }
        }
    } else {
        mp_bluetooth_gap_on_set_secret(
            MP_BLUETOOTH_ZEPHYR_SECRET_KEYS,
            (const uint8_t *)&le_addr, sizeof(bt_addr_le_t),
            NULL, 0);
    }
    #endif

    return bt_unpair(BT_ID_DEFAULT, addr == NULL ? NULL : &le_addr);
}

int mp_bluetooth_gap_passkey(uint16_t conn_handle, uint8_t action, mp_int_t passkey) {
    DEBUG_printf("mp_bluetooth_gap_passkey: conn_handle=%d action=%d passkey=%d\n",
                 conn_handle, action, (int)passkey);

    if (!mp_bluetooth_is_active()) {
        return ERRNO_BLUETOOTH_NOT_ACTIVE;
    }

    struct bt_conn *conn = mp_bt_zephyr_get_conn(conn_handle);
    if (conn == NULL) {
        return MP_ENOTCONN;
    }

    int err = 0;

    switch (action) {
        case MP_BLUETOOTH_PASSKEY_ACTION_INPUT:
            // User entered passkey that was displayed by the remote device
            err = bt_conn_auth_passkey_entry(conn, (unsigned int)passkey);
            break;

        case MP_BLUETOOTH_PASSKEY_ACTION_DISPLAY:
            // Passkey was already displayed to user via callback - nothing to submit
            // The remote device will enter the passkey, and Zephyr verifies it automatically
            err = 0;  // No-op, success
            break;

        case MP_BLUETOOTH_PASSKEY_ACTION_NUMERIC_COMPARISON:
            // User confirmed numeric comparison
            if (passkey != 0) {
                // Non-zero means user confirmed passkey matches
                err = bt_conn_auth_passkey_confirm(conn);
            } else {
                // Zero means user rejected/cancelled
                err = bt_conn_auth_cancel(conn);
            }
            break;

        case MP_BLUETOOTH_PASSKEY_ACTION_NONE:
            // Just Works pairing - confirm
            err = bt_conn_auth_pairing_confirm(conn);
            break;

        default:
            return MP_EINVAL;
    }

    return bt_err_to_errno(err);
}

void mp_bluetooth_set_bonding(bool enabled) {
    mp_bt_zephyr_bonding = enabled;
    DEBUG_printf("mp_bluetooth_set_bonding: enabled=%d\n", enabled);
    // Set Zephyr's global bondable flag. This controls whether SMP_FLAG_BOND
    // is set during pairing, which determines the bonded flag in pairing_complete.
    bt_set_bondable(enabled);
}

void mp_bluetooth_set_le_secure(bool enabled) {
    mp_bt_zephyr_le_secure = enabled;
    DEBUG_printf("mp_bluetooth_set_le_secure: enabled=%d (SC %s)\n",
                 enabled, enabled ? "required" : "optional");
    // When enabled, mp_bluetooth_gap_pair() will use BT_SECURITY_L4 (SC required)
    // When disabled, mp_bluetooth_gap_pair() will use BT_SECURITY_L3 (allow legacy)
}

void mp_bluetooth_set_mitm_protection(bool enabled) {
    mp_bt_zephyr_mitm_protection = enabled;
    DEBUG_printf("mp_bluetooth_set_mitm_protection: enabled=%d\n", enabled);
    // When enabled, mp_bluetooth_gap_pair() will use BT_SECURITY_L3/L4 (MITM required)
    // When disabled, mp_bluetooth_gap_pair() will use BT_SECURITY_L2 (Just Works)
}

void mp_bluetooth_set_io_capability(uint8_t capability) {
    DEBUG_printf("mp_bluetooth_set_io_capability: capability=%d\n", capability);

    mp_bt_zephyr_io_capability = capability;

    // Configure auth callbacks based on IO capability
    // IO capability values (from MicroPython BLE API):
    // 0 = NO_INPUT_NO_OUTPUT (Just Works)
    // 1 = DISPLAY_ONLY (passkey display)
    // 2 = KEYBOARD_ONLY (passkey entry)
    // 3 = DISPLAY_YESNO (numeric comparison)
    // 4 = KEYBOARD_DISPLAY (all methods)

    switch (capability) {
        case 0:  // NO_INPUT_NO_OUTPUT (Just Works)
            mp_bt_zephyr_auth_callbacks.passkey_display = NULL;
            mp_bt_zephyr_auth_callbacks.passkey_entry = NULL;
            mp_bt_zephyr_auth_callbacks.passkey_confirm = NULL;
            mp_bt_zephyr_auth_callbacks.pairing_confirm = zephyr_pairing_confirm_cb;
            mp_bt_zephyr_auth_callbacks.cancel = zephyr_auth_cancel_cb;
            break;

        case 1:  // DISPLAY_ONLY
            mp_bt_zephyr_auth_callbacks.passkey_display = zephyr_passkey_display_cb;
            mp_bt_zephyr_auth_callbacks.passkey_entry = NULL;
            mp_bt_zephyr_auth_callbacks.passkey_confirm = NULL;
            mp_bt_zephyr_auth_callbacks.pairing_confirm = zephyr_pairing_confirm_cb;
            mp_bt_zephyr_auth_callbacks.cancel = zephyr_auth_cancel_cb;
            break;

        case 2:  // KEYBOARD_ONLY
            mp_bt_zephyr_auth_callbacks.passkey_display = NULL;
            mp_bt_zephyr_auth_callbacks.passkey_entry = zephyr_passkey_entry_cb;
            mp_bt_zephyr_auth_callbacks.passkey_confirm = NULL;
            mp_bt_zephyr_auth_callbacks.pairing_confirm = zephyr_pairing_confirm_cb;
            mp_bt_zephyr_auth_callbacks.cancel = zephyr_auth_cancel_cb;
            break;

        case 3:  // DISPLAY_YESNO (numeric comparison)
            mp_bt_zephyr_auth_callbacks.passkey_display = zephyr_passkey_display_cb;
            mp_bt_zephyr_auth_callbacks.passkey_entry = NULL;
            mp_bt_zephyr_auth_callbacks.passkey_confirm = zephyr_passkey_confirm_cb;
            mp_bt_zephyr_auth_callbacks.pairing_confirm = zephyr_pairing_confirm_cb;
            mp_bt_zephyr_auth_callbacks.cancel = zephyr_auth_cancel_cb;
            break;

        case 4:  // KEYBOARD_DISPLAY (all methods)
        default:
            mp_bt_zephyr_auth_callbacks.passkey_display = zephyr_passkey_display_cb;
            mp_bt_zephyr_auth_callbacks.passkey_entry = zephyr_passkey_entry_cb;
            mp_bt_zephyr_auth_callbacks.passkey_confirm = zephyr_passkey_confirm_cb;
            mp_bt_zephyr_auth_callbacks.pairing_confirm = zephyr_pairing_confirm_cb;
            mp_bt_zephyr_auth_callbacks.cancel = zephyr_auth_cancel_cb;
            break;
    }

    // Re-register callbacks if BLE is already active
    // Note: bt_conn_auth_cb_register() can be called multiple times to update callbacks
    if (mp_bluetooth_is_active()) {
        bt_conn_auth_cb_register(&mp_bt_zephyr_auth_callbacks);
        DEBUG_printf("Auth callbacks re-registered for IO capability %d\n", capability);
    }
}

// ============================================================================
// L2CAP Connection-Oriented Channels (COC) Implementation
// ============================================================================

#if MICROPY_PY_BLUETOOTH_ENABLE_L2CAP_CHANNELS

// Helper to get connection handle from bt_l2cap_chan
static uint16_t l2cap_chan_get_conn_handle(struct bt_l2cap_chan *chan) {
    if (!chan || !chan->conn) {
        return 0xFFFF;
    }
    // Use mp_bt_zephyr_conn_to_handle to get the MicroPython connection index
    // (info.id returns the local identity address, not the connection handle)
    return mp_bt_zephyr_conn_to_handle(chan->conn);
}

// Helper to get L2CAP channel for a given conn_handle and cid
static mp_bluetooth_zephyr_l2cap_channel_t *l2cap_get_channel_for_conn_cid(
    uint16_t conn_handle, uint16_t cid) {

    mp_bluetooth_zephyr_root_pointers_t *rp = MP_STATE_PORT(bluetooth_zephyr_root_pointers);
    if (!rp || !rp->l2cap_chan) {
        DEBUG_printf("l2cap_get_channel: no channel\n");
        return NULL;
    }

    struct bt_l2cap_le_chan *le_chan = &rp->l2cap_chan->le_chan;

    // Verify conn_handle and cid match
    uint16_t chan_conn = l2cap_chan_get_conn_handle(&le_chan->chan);
    if (chan_conn != conn_handle) {
        DEBUG_printf("l2cap_get_channel: conn mismatch %d != %d\n", chan_conn, conn_handle);
        return NULL;
    }
    if (le_chan->rx.cid != cid) {
        DEBUG_printf("l2cap_get_channel: cid mismatch %d != %d\n", le_chan->rx.cid, cid);
        return NULL;
    }

    return rp->l2cap_chan;
}

// Allocate and initialize a new L2CAP channel structure
static int l2cap_create_channel(uint16_t mtu, mp_bluetooth_zephyr_l2cap_channel_t **out) {
    mp_bluetooth_zephyr_root_pointers_t *rp = MP_STATE_PORT(bluetooth_zephyr_root_pointers);

    if (rp->l2cap_chan != NULL) {
        // Only one L2CAP channel allowed at a time (matches NimBLE)
        DEBUG_printf("l2cap_create_channel: channel already in use\n");
        return MP_EALREADY;
    }

    // Allocate channel structure
    mp_bluetooth_zephyr_l2cap_channel_t *chan = m_new0(mp_bluetooth_zephyr_l2cap_channel_t, 1);
    if (!chan) {
        return MP_ENOMEM;
    }

    // Allocate RX accumulation buffer
    chan->rx_buf = m_new(uint8_t, L2CAP_RX_BUF_SIZE);
    if (!chan->rx_buf) {
        m_del(mp_bluetooth_zephyr_l2cap_channel_t, chan, 1);
        return MP_ENOMEM;
    }

    // Clamp MTU to what our compile-time buffer pool can handle.
    if (mtu > CONFIG_BT_L2CAP_TX_MTU) {
        mtu = CONFIG_BT_L2CAP_TX_MTU;
    }

    // Initialize channel state
    chan->mtu = mtu;
    chan->rx_len = 0;

    // Set up the Zephyr channel with our callbacks (matching Zephyr example pattern)
    chan->le_chan.chan.ops = &l2cap_chan_ops;

    // Set RX MTU - this is what we advertise to the peer
    // Note: Don't set MPS explicitly - Zephyr will derive it from config
    chan->le_chan.rx.mtu = mtu;

    rp->l2cap_chan = chan;
    *out = chan;
    return 0;
}

// Free L2CAP channel (always clean up channel, keep server if listening)
static void l2cap_destroy_channel(void) {
    mp_bluetooth_zephyr_root_pointers_t *rp = MP_STATE_PORT(bluetooth_zephyr_root_pointers);
    if (!rp || !rp->l2cap_chan) {
        return;
    }

    // Save pointer and clear root pointer first to prevent concurrent access
    mp_bluetooth_zephyr_l2cap_channel_t *chan = rp->l2cap_chan;
    rp->l2cap_chan = NULL;

    // Free RX accumulation buffer
    if (chan->rx_buf) {
        m_del(uint8_t, chan->rx_buf, L2CAP_RX_BUF_SIZE);
    }

    // Free channel structure
    m_del(mp_bluetooth_zephyr_l2cap_channel_t, chan, 1);
}

// --- L2CAP Callbacks ---

static void l2cap_connected_cb(struct bt_l2cap_chan *chan) {
    DEBUG_printf("l2cap_connected_cb: chan=%p\n", chan);

    if (!mp_bluetooth_is_active()) {
        return;
    }

    struct bt_l2cap_le_chan *le_chan = BT_L2CAP_LE_CHAN(chan);
    uint16_t conn_handle = l2cap_chan_get_conn_handle(chan);

    DEBUG_printf("l2cap_connected_cb: conn=%d rx_cid=%d tx_cid=%d rx_mtu=%d tx_mtu=%d credits=%ld\n",
                 conn_handle, le_chan->rx.cid, le_chan->tx.cid,
                 le_chan->rx.mtu, le_chan->tx.mtu, atomic_get(&le_chan->tx.credits));

    // Notify MicroPython about the connection
    // our_mtu is rx.mtu, peer_mtu is tx.mtu
    mp_bluetooth_on_l2cap_connect(conn_handle,
                                   le_chan->rx.cid,
                                   le_chan->psm,
                                   le_chan->rx.mtu,  // our_mtu
                                   le_chan->tx.mtu); // peer_mtu
}

static void l2cap_disconnected_cb(struct bt_l2cap_chan *chan) {
    DEBUG_printf("l2cap_disconnected_cb\n");

    struct bt_l2cap_le_chan *le_chan = BT_L2CAP_LE_CHAN(chan);
    uint16_t conn_handle = l2cap_chan_get_conn_handle(chan);

    DEBUG_printf("l2cap_disconnected_cb: conn=%d cid=%d active=%d\n",
                 conn_handle, le_chan->rx.cid, mp_bluetooth_is_active());

    // Only notify Python if BLE is still active (not during deinit)
    if (mp_bluetooth_is_active()) {
        mp_bluetooth_on_l2cap_disconnect(conn_handle,
                                          le_chan->rx.cid,
                                          le_chan->psm,
                                          0); // status=0 for normal disconnect
    }

    // Always clean up channel resources, even during deinit.
    // This ensures Zephyr's internal state is properly cleaned up.
    l2cap_destroy_channel();
}

static int l2cap_recv_cb(struct bt_l2cap_chan *chan, struct net_buf *buf) {
    DEBUG_printf("l2cap_recv_cb: len=%d active=%d\n", (int)buf->len, mp_bluetooth_is_active());

    // During deinit, just return 0 to let Zephyr reclaim the buffer.
    // Don't return errors that might confuse Zephyr's state machine.
    if (!mp_bluetooth_is_active()) {
        return 0;
    }

    mp_bluetooth_zephyr_root_pointers_t *rp = MP_STATE_PORT(bluetooth_zephyr_root_pointers);
    if (!rp || !rp->l2cap_chan) {
        return 0;  // Return 0 instead of error to allow cleanup
    }

    mp_bluetooth_zephyr_l2cap_channel_t *l2cap_chan = rp->l2cap_chan;
    struct bt_l2cap_le_chan *le_chan = BT_L2CAP_LE_CHAN(chan);
    uint16_t conn_handle = l2cap_chan_get_conn_handle(chan);

    // Copy data to our accumulation buffer
    size_t add_len = buf->len;
    size_t avail = L2CAP_RX_BUF_SIZE - l2cap_chan->rx_len;
    if (avail >= add_len) {
        memcpy(l2cap_chan->rx_buf + l2cap_chan->rx_len, buf->data, add_len);
        l2cap_chan->rx_len += add_len;
        DEBUG_printf("l2cap_recv_cb: added %d, total=%d\n", (int)add_len, (int)l2cap_chan->rx_len);
    } else {
        DEBUG_printf("l2cap_recv_cb: buffer full, dropping %d bytes\n", (int)add_len);
    }

    // Notify MicroPython that data is available
    mp_bluetooth_on_l2cap_recv(conn_handle, le_chan->rx.cid);

    // Return 0 to grant credits immediately
    // We've copied data to our own buffer, so Zephyr can reuse this buffer
    return 0;
}

// Sent callback - fires when an SDU has been fully transmitted.
// Always notify Python that the channel is ready for more data.
// This eliminates race conditions with flag-based stall tracking — the event
// accumulates harmlessly in Python's waiting_events if nobody is waiting.
static void l2cap_sent_cb(struct bt_l2cap_chan *chan) {
    DEBUG_printf("l2cap_sent_cb\n");

    if (!mp_bluetooth_is_active()) {
        return;
    }

    struct bt_l2cap_le_chan *le_chan = BT_L2CAP_LE_CHAN(chan);
    uint16_t conn_handle = l2cap_chan_get_conn_handle(chan);
    mp_bluetooth_on_l2cap_send_ready(conn_handle, le_chan->rx.cid, 0);
}

// Status callback - called when channel status changes (e.g., credits become available).
// Not used for flow control — Zephyr handles credit management internally via
// bt_l2cap_chan_send queueing.  Kept for debug visibility.
static void l2cap_status_cb(struct bt_l2cap_chan *chan, atomic_t *status) {
    DEBUG_printf("l2cap_status_cb: can_send=%d\n",
                 atomic_test_bit(status, BT_L2CAP_STATUS_OUT));
}

static struct net_buf *l2cap_alloc_buf_cb(struct bt_l2cap_chan *chan) {
    // Allocate from our SDU pool
    struct net_buf *buf = net_buf_alloc(&l2cap_sdu_pool, K_NO_WAIT);
    DEBUG_printf("l2cap_alloc_buf_cb: %s\n", buf ? "OK" : "FAIL");
    return buf;
}

static int l2cap_server_accept_cb(struct bt_conn *conn, struct bt_l2cap_server *server,
                                  struct bt_l2cap_chan **chan) {
    DEBUG_printf("l2cap_server_accept_cb\n");

    if (!mp_bluetooth_is_active()) {
        return -ESHUTDOWN;
    }

    // Use static server structure (persists across soft resets)
    if (!mp_bluetooth_zephyr_l2cap_server_registered) {
        DEBUG_printf("l2cap_server_accept_cb: server not registered\n");
        return -EINVAL;
    }

    // Check that Python has called l2cap_listen() this session.
    // If not, reject the connection - the Zephyr server persists but Python
    // hasn't set up handlers yet.
    mp_bluetooth_zephyr_root_pointers_t *rp = MP_STATE_PORT(bluetooth_zephyr_root_pointers);
    if (!rp || !rp->l2cap_listening) {
        DEBUG_printf("l2cap_server_accept_cb: not listening this session\n");
        return -EINVAL;  // Use EINVAL instead of ENOENT for consistency
    }

    // On native Zephyr, this callback runs on the BT RX thread which doesn't
    // hold the MicroPython GIL. We need the GIL because l2cap_create_channel()
    // allocates from the GC heap (m_new) and mp_bluetooth_on_l2cap_accept()
    // invokes the Python IRQ handler. Zephyr mutexes are recursive, so this
    // is safe even though invoke_irq_handler also acquires the GIL.
    #if MICROPY_PY_BLUETOOTH_USE_SYNC_EVENTS_WITH_INTERLOCK
    mp_state_thread_t *ts_orig = mp_thread_get_state();
    mp_state_thread_t ts;
    if (ts_orig == NULL) {
        mp_thread_init_state(&ts, MICROPY_PY_BLUETOOTH_SYNC_EVENT_STACK_SIZE, NULL, NULL);
        MP_THREAD_GIL_ENTER();
    }
    #endif

    int result = -EINVAL;

    // Get connection handle (MicroPython connection index, not HCI handle)
    uint16_t conn_handle = mp_bt_zephyr_conn_to_handle(conn);
    if (conn_handle == 0xFFFF) {
        goto done;
    }

    // Create a channel for this incoming connection
    mp_bluetooth_zephyr_l2cap_channel_t *l2cap_chan;
    int ret = l2cap_create_channel(mp_bluetooth_zephyr_l2cap_static_server.mtu, &l2cap_chan);
    if (ret != 0) {
        result = ret;
        goto done;
    }

    // Set the PSM on the channel
    l2cap_chan->le_chan.psm = mp_bluetooth_zephyr_l2cap_static_server.server.psm;

    // Let MicroPython decide whether to accept
    // Note: Zephyr doesn't give us peer MTU at accept time, so we use our MTU for both
    // Note: CID may not be assigned yet - we'll get the real CID in connected callback
    DEBUG_printf("l2cap_server_accept_cb: cid=%d (may be 0 at accept time)\n",
                 l2cap_chan->le_chan.rx.cid);
    ret = mp_bluetooth_on_l2cap_accept(conn_handle,
                                        l2cap_chan->le_chan.rx.cid,
                                        mp_bluetooth_zephyr_l2cap_static_server.server.psm,
                                        l2cap_chan->mtu,  // our_mtu
                                        0);               // peer_mtu (not known yet)
    if (ret != 0) {
        // Application rejected the connection
        l2cap_destroy_channel();
        result = ret;
        goto done;
    }

    // Return our channel to Zephyr
    *chan = &l2cap_chan->le_chan.chan;
    result = 0;

done:
    #if MICROPY_PY_BLUETOOTH_USE_SYNC_EVENTS_WITH_INTERLOCK
    if (ts_orig == NULL) {
        MP_THREAD_GIL_EXIT();
        mp_thread_set_state(ts_orig);
    }
    #endif
    return result;
}

// --- L2CAP API Functions ---

int mp_bluetooth_l2cap_listen(uint16_t psm, uint16_t mtu) {
    DEBUG_printf("mp_bluetooth_l2cap_listen: psm=%d mtu=%d\n", psm, mtu);

    if (!mp_bluetooth_is_active()) {
        return ERRNO_BLUETOOTH_NOT_ACTIVE;
    }

    mp_bluetooth_zephyr_root_pointers_t *rp = MP_STATE_PORT(bluetooth_zephyr_root_pointers);

    // Check if already listening (this session)
    if (rp->l2cap_listening) {
        return MP_EALREADY;
    }

    // Check if server was already registered (persists across soft reset)
    // Zephyr has no bt_l2cap_server_unregister() for LE L2CAP, so once registered
    // the PSM stays registered until hard reset.
    if (mp_bluetooth_zephyr_l2cap_server_registered) {
        if (mp_bluetooth_zephyr_l2cap_static_server.server.psm == psm) {
            // Same PSM - just update MTU and mark as listening
            DEBUG_printf("mp_bluetooth_l2cap_listen: reusing existing server for PSM %d\n", psm);
            mp_bluetooth_zephyr_l2cap_static_server.mtu = MIN(mtu, CONFIG_BT_L2CAP_TX_MTU);
            rp->l2cap_listening = true;
            return 0;
        } else {
            // Different PSM requested but another is already registered
            DEBUG_printf("mp_bluetooth_l2cap_listen: server already registered for PSM %d\n",
                        mp_bluetooth_zephyr_l2cap_static_server.server.psm);
            return MP_EADDRINUSE;
        }
    }

    // Set up static server structure
    mp_bluetooth_zephyr_l2cap_static_server.server.psm = psm;
    mp_bluetooth_zephyr_l2cap_static_server.server.accept = l2cap_server_accept_cb;
    mp_bluetooth_zephyr_l2cap_static_server.server.sec_level = BT_SECURITY_L1;  // No encryption required
    mp_bluetooth_zephyr_l2cap_static_server.mtu = MIN(mtu, CONFIG_BT_L2CAP_TX_MTU);

    // Register the server
    int ret = bt_l2cap_server_register(&mp_bluetooth_zephyr_l2cap_static_server.server);
    if (ret != 0) {
        DEBUG_printf("mp_bluetooth_l2cap_listen: bt_l2cap_server_register failed %d\n", ret);
        return bt_err_to_errno(ret);
    }

    mp_bluetooth_zephyr_l2cap_server_registered = true;
    rp->l2cap_listening = true;
    DEBUG_printf("mp_bluetooth_l2cap_listen: listening on PSM %d\n", psm);
    return 0;
}

int mp_bluetooth_l2cap_connect(uint16_t conn_handle, uint16_t psm, uint16_t mtu) {
    DEBUG_printf("mp_bluetooth_l2cap_connect: conn_handle=%d psm=%d mtu=%d\n",
                 conn_handle, psm, mtu);

    if (!mp_bluetooth_is_active()) {
        return ERRNO_BLUETOOTH_NOT_ACTIVE;
    }

    struct bt_conn *conn = mp_bt_zephyr_get_conn(conn_handle);
    if (conn == NULL) {
        return MP_ENOTCONN;
    }

    // Create channel structure
    mp_bluetooth_zephyr_l2cap_channel_t *chan;
    int ret = l2cap_create_channel(mtu, &chan);
    if (ret != 0) {
        return ret;
    }

    // Initiate connection
    ret = bt_l2cap_chan_connect(conn, &chan->le_chan.chan, psm);
    if (ret != 0) {
        DEBUG_printf("mp_bluetooth_l2cap_connect: bt_l2cap_chan_connect failed %d\n", ret);
        l2cap_destroy_channel();  // Clean up the channel we just created
        return bt_err_to_errno(ret);
    }

    // Process work queue to send the connection request
    mp_bluetooth_zephyr_work_process();

    return 0;
}

int mp_bluetooth_l2cap_disconnect(uint16_t conn_handle, uint16_t cid) {
    DEBUG_printf("mp_bluetooth_l2cap_disconnect: conn_handle=%d cid=%d\n", conn_handle, cid);

    if (!mp_bluetooth_is_active()) {
        return ERRNO_BLUETOOTH_NOT_ACTIVE;
    }

    mp_bluetooth_zephyr_l2cap_channel_t *chan = l2cap_get_channel_for_conn_cid(conn_handle, cid);
    if (!chan) {
        return MP_EINVAL;
    }

    int ret = bt_l2cap_chan_disconnect(&chan->le_chan.chan);
    if (ret != 0) {
        DEBUG_printf("mp_bluetooth_l2cap_disconnect: bt_l2cap_chan_disconnect failed %d\n", ret);
        return bt_err_to_errno(ret);
    }

    // Process work queue
    mp_bluetooth_zephyr_work_process();

    return 0;
}

int mp_bluetooth_l2cap_send(uint16_t conn_handle, uint16_t cid,
                            const uint8_t *buf, size_t len, bool *stalled) {
    DEBUG_printf("mp_bluetooth_l2cap_send: conn=%d cid=%d len=%d\n", conn_handle, cid, (int)len);

    if (!mp_bluetooth_is_active()) {
        return ERRNO_BLUETOOTH_NOT_ACTIVE;
    }

    mp_bluetooth_zephyr_l2cap_channel_t *chan = l2cap_get_channel_for_conn_cid(conn_handle, cid);
    if (!chan) {
        return MP_EINVAL;
    }

    struct bt_l2cap_le_chan *le_chan = &chan->le_chan;

    // Check that the data fits in the peer's MTU and our local buffer pool.
    if (len > le_chan->tx.mtu || len > CONFIG_BT_L2CAP_TX_MTU) {
        return MP_EINVAL;
    }

    // Allocate buffer from our pool.  K_NO_WAIT because we hold the GIL and
    // l2cap_sent_cb (which frees buffers) needs the GIL on native Zephyr.
    struct net_buf *sdu_buf = net_buf_alloc(&l2cap_sdu_pool, K_NO_WAIT);
    if (!sdu_buf) {
        // Pool exhausted — cannot accept data.  Return error so Python knows
        // the payload was NOT consumed (unlike *stalled which means "accepted
        // but wait before sending more").
        DEBUG_printf("mp_bluetooth_l2cap_send: pool exhausted\n");
        return MP_ENOMEM;
    }

    // Reserve headroom for L2CAP SDU header
    net_buf_reserve(sdu_buf, BT_L2CAP_SDU_CHAN_SEND_RESERVE);

    // Copy data into buffer
    net_buf_add_mem(sdu_buf, buf, len);

    // Send — Zephyr handles credit-based flow control internally.
    // The SDU is queued and transmitted as credits become available.
    // l2cap_sent_cb fires when the SDU is fully consumed.
    int ret = bt_l2cap_chan_send(&le_chan->chan, sdu_buf);
    if (ret < 0) {
        DEBUG_printf("mp_bluetooth_l2cap_send: error %d\n", ret);
        net_buf_unref(sdu_buf);
        return bt_err_to_errno(ret);
    }

    // Process work queue (no-op on native Zephyr, needed for HAL builds)
    mp_bluetooth_zephyr_work_process();

    // Data accepted.  Always stall after each send so Python waits for
    // l2cap_sent_cb (SEND_READY) before sending more.  This ensures at most
    // one SDU is in-flight at a time, preventing net_buf pool exhaustion and
    // avoiding the race condition where l2cap_sent_cb fires between the pool
    // check and the stall flag being set.  Throughput is still adequate —
    // each send completes within 1-2 BLE connection events (~30-60ms).
    // TODO: Could allow 2-3 in-flight SDUs via atomic counter instead of
    // boolean stall for higher throughput.
    *stalled = true;

    return 0;
}

int mp_bluetooth_l2cap_recvinto(uint16_t conn_handle, uint16_t cid,
                                 uint8_t *buf, size_t *len) {
    DEBUG_printf("mp_bluetooth_l2cap_recvinto: conn_handle=%d cid=%d buf=%p len=%zu\n",
                 conn_handle, cid, buf, buf ? *len : 0);

    if (!mp_bluetooth_is_active()) {
        return ERRNO_BLUETOOTH_NOT_ACTIVE;
    }

    mp_bluetooth_zephyr_l2cap_channel_t *chan = l2cap_get_channel_for_conn_cid(conn_handle, cid);
    if (!chan) {
        return MP_EINVAL;
    }

    MICROPY_PY_BLUETOOTH_ENTER

    if (chan->rx_len > 0) {
        size_t avail = chan->rx_len;

        if (buf == NULL) {
            // Just return the amount of data available
            *len = avail;
        } else {
            // Copy data into buffer
            size_t to_copy = MIN(*len, avail);
            memcpy(buf, chan->rx_buf, to_copy);
            *len = to_copy;

            if (to_copy == avail) {
                // All data consumed - reset buffer
                chan->rx_len = 0;
            } else {
                // Partial consumption - shift remaining data to front
                memmove(chan->rx_buf, chan->rx_buf + to_copy, avail - to_copy);
                chan->rx_len = avail - to_copy;
            }
        }
    } else {
        // No pending data
        *len = 0;
    }

    MICROPY_PY_BLUETOOTH_EXIT

    return 0;
}

#endif // MICROPY_PY_BLUETOOTH_ENABLE_L2CAP_CHANNELS

MP_REGISTER_ROOT_POINTER(struct _mp_bluetooth_zephyr_root_pointers_t *bluetooth_zephyr_root_pointers);

#endif // MICROPY_PY_BLUETOOTH
