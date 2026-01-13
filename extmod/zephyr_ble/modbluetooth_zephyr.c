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
#include <zephyr/device.h>
#include "extmod/modbluetooth.h"
#include "extmod/zephyr_ble/hal/zephyr_ble_work.h"
#include "extmod/zephyr_ble/net_buf_pool_registry.h"

// Access Zephyr's internal bt_dev for force-reset on deinit failure
// The include path should have lib/zephyr/subsys/bluetooth/host already
#include "hci_core.h"

#if MICROPY_PY_NETWORK_CYW43
// For cyw43_t definition (bt_loaded reset on deinit failure)
#include "lib/cyw43-driver/src/cyw43.h"
#endif

// HCI RX task functions (implemented in port-specific mpzephyrport_*.c)
extern void mp_bluetooth_zephyr_hci_rx_task_start(void);
extern void mp_bluetooth_zephyr_hci_rx_task_stop(void);

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

    // GATT client write state
    struct bt_gatt_write_params gattc_write_params;
    uint16_t gattc_write_conn_handle;
    uint16_t gattc_write_value_handle;

    // MTU exchange state
    struct bt_gatt_exchange_params gattc_mtu_params;
    uint16_t gattc_mtu_conn_handle;
    #endif // MICROPY_PY_BLUETOOTH_ENABLE_GATT_CLIENT
} mp_bluetooth_zephyr_root_pointers_t;

static int mp_bluetooth_zephyr_ble_state;

// BLE initialization completion tracking (-1 = pending, 0 = success, >0 = error code)
static volatile int mp_bluetooth_zephyr_bt_enable_result = -1;

// Track if Zephyr callbacks are registered (persists across bt_enable/bt_disable cycles)
static bool mp_bt_zephyr_callbacks_registered = false;

// Timeout for BLE initialization (milliseconds)
#define ZEPHYR_BLE_STARTUP_TIMEOUT 5000

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

static mp_bt_zephyr_conn_t *mp_bt_zephyr_next_conn;

static mp_bt_zephyr_conn_t *mp_bt_zephyr_find_connection(uint8_t conn_handle);
static void mp_bt_zephyr_insert_connection(mp_bt_zephyr_conn_t *connection);
static void mp_bt_zephyr_remove_connection(uint8_t conn_handle);
static void mp_bt_zephyr_connected(struct bt_conn *connected, uint8_t err);
static void mp_bt_zephyr_disconnected(struct bt_conn *disconn, uint8_t reason);
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

static struct bt_conn_cb mp_bt_zephyr_conn_callbacks = {
    .connected = mp_bt_zephyr_connected,
    .disconnected = mp_bt_zephyr_disconnected,
};

static mp_bt_zephyr_conn_t *mp_bt_zephyr_find_connection(uint8_t conn_handle) {
    struct bt_conn_info info;
    for (mp_bt_zephyr_conn_t *connection = MP_STATE_PORT(bluetooth_zephyr_root_pointers)->connections; connection != NULL; connection = connection->next) {
        if (connection->conn) {
            bt_conn_get_info(connection->conn, &info);
            if (info.id == conn_handle) {
                return connection;
            }
        }
    }
    return NULL;
}

static void mp_bt_zephyr_insert_connection(mp_bt_zephyr_conn_t *connection) {
    connection->next = MP_STATE_PORT(bluetooth_zephyr_root_pointers)->connections;
    MP_STATE_PORT(bluetooth_zephyr_root_pointers)->connections = connection;
}

static void mp_bt_zephyr_remove_connection(uint8_t conn_handle) {
    struct bt_conn_info info;
    mp_bt_zephyr_conn_t *prev = NULL;
    for (mp_bt_zephyr_conn_t *connection = MP_STATE_PORT(bluetooth_zephyr_root_pointers)->connections; connection != NULL; connection = connection->next) {
        if (connection->conn) {
            bt_conn_get_info(connection->conn, &info);
            if (info.id == conn_handle) {
                // unlink this item and the gc will eventually collect it
                if (prev != NULL) {
                    prev->next = connection->next;
                } else {
                    // move the start pointer
                    MP_STATE_PORT(bluetooth_zephyr_root_pointers)->connections = connection->next;
                }
                break;
            } else {
                prev = connection;
            }
        }
    }
}

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
        // For outgoing connections, clean up stored conn reference
        if (mp_bt_zephyr_next_conn->conn != NULL) {
            DEBUG_printf("  Unref'ing failed outgoing connection %p\n", mp_bt_zephyr_next_conn->conn);
            bt_conn_unref(mp_bt_zephyr_next_conn->conn);
            mp_bt_zephyr_next_conn->conn = NULL;
        }
        // Don't free mp_bt_zephyr_next_conn here - it's registered with GC list
        // Reset pointer so next connection allocates fresh structure
        mp_bt_zephyr_next_conn = NULL;
        mp_bluetooth_gap_on_connected_disconnected(disconnect_event, info.id, 0xff, addr);
    } else {
        DEBUG_printf("Connected with id %d role %d\n", info.id, info.role);
        // Take a reference to the connection for storage
        // For incoming connections (peripheral role), conn is NULL - take new ref
        // For outgoing connections (central role), conn already stored - use existing ref
        if (mp_bt_zephyr_next_conn->conn == NULL) {
            // Incoming connection: callback parameter is borrowed, need our own ref
            mp_bt_zephyr_next_conn->conn = bt_conn_ref(conn);
            DEBUG_printf("  Stored NEW connection ref %p\n", mp_bt_zephyr_next_conn->conn);
        } else {
            // Outgoing connection: already have ref from bt_conn_le_create()
            DEBUG_printf("  Using EXISTING connection ref %p\n", mp_bt_zephyr_next_conn->conn);
        }
        debug_check_uuid("before_connect_cb");
        mp_bluetooth_gap_on_connected_disconnected(connect_event, info.id, info.le.dst->type, info.le.dst->a.val);
        debug_check_uuid("after_connect_cb");
        mp_bt_zephyr_insert_connection(mp_bt_zephyr_next_conn);
        // Reset pointer so next connection allocates fresh structure
        mp_bt_zephyr_next_conn = NULL;
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

    // Determine correct IRQ event based on connection role:
    // - BT_HCI_ROLE_CENTRAL (0x00): Local initiated connection → PERIPHERAL_DISCONNECT
    // - BT_HCI_ROLE_PERIPHERAL (0x01): Remote initiated connection → CENTRAL_DISCONNECT
    uint16_t disconnect_event = (info.role == BT_HCI_ROLE_CENTRAL)
        ? MP_BLUETOOTH_IRQ_PERIPHERAL_DISCONNECT
        : MP_BLUETOOTH_IRQ_CENTRAL_DISCONNECT;

    DEBUG_printf("Disconnected (id %d reason %u role %d)\n", info.id, reason, info.role);

    // Find our stored connection and unref it
    // Note: 'conn' parameter is a borrowed reference from Zephyr callback - don't unref it
    // We only unref the reference we explicitly took in mp_bt_zephyr_connected()
    mp_bt_zephyr_conn_t *stored = mp_bt_zephyr_find_connection(info.id);
    if (stored && stored->conn) {
        DEBUG_printf("  Unref'ing stored connection %p\n", stored->conn);
        bt_conn_unref(stored->conn);
        stored->conn = NULL;
    }

    mp_bt_zephyr_remove_connection(info.id);
    mp_bluetooth_gap_on_connected_disconnected(disconnect_event, info.id, info.le.dst->type, info.le.dst->a.val);
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

// Callback for bt_enable() completion
static void mp_bluetooth_zephyr_bt_ready_cb(int err) {
    DEBUG_printf("bt_ready_cb: err=%d\n", err);
    mp_bluetooth_zephyr_bt_enable_result = err;
}

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
        // FIXED: Don't call mp_event_wait_indefinite() - we're already in scheduler!
        // Instead, directly process work queue to make space
        extern void mp_bluetooth_zephyr_poll(void);
        mp_bluetooth_zephyr_poll();
    }
    // Indicate scanning has stopped so that no more scan result events are generated
    // (they may still come in until bt_le_scan_stop is called by gap_scan_stop).
    mp_bluetooth_zephyr_gap_scan_state = MP_BLUETOOTH_ZEPHYR_GAP_SCAN_STATE_DEACTIVATING;
}
#endif

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
            #if MICROPY_PY_NETWORK_CYW43 || MICROPY_PY_BLUETOOTH_USE_ZEPHYR_HCI
            extern void mp_bluetooth_zephyr_port_init(void);
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

                mp_bt_zephyr_callbacks_registered = true;
                DEBUG_printf("Zephyr callbacks registered\n");
            }
        }

        // Initialize Zephyr BLE host stack
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

        // Debug: check HCI RX task status
        extern bool mp_bluetooth_zephyr_hci_rx_task_active(void);
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
                    DEBUG_printf("Got init work=%p, handler=%p\n", init_work, init_work->handler);
                    DEBUG_SEQ_printf("Executing init work handler\n");
                    DEBUG_ENTER("init_work->handler");
                    // Set work queue context so k_current_get() returns &k_sys_work_q.thread
                    // This enables Zephyr's synchronous HCI command path
                    extern void mp_bluetooth_zephyr_set_sys_work_q_context(bool in_context);
                    mp_bluetooth_zephyr_set_sys_work_q_context(true);
                    init_work->handler(init_work);
                    mp_bluetooth_zephyr_set_sys_work_q_context(false);
                    DEBUG_EXIT("init_work->handler");
                    DEBUG_printf("Init work handler completed\n");
                    DEBUG_SEQ_printf("Init work handler done\n");
                    // Handler has completed (bt_init ran to completion)
                    // bt_ready_cb should have been called and set result flag
                } else {
                    DEBUG_printf("No init work available (work=%p)\n", init_work);
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

        // Check result
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

    init_complete:
        DEBUG_printf("BLE initialization successful!\n");

        // HCI RX task disabled - causes hangs during scan stop
        // TODO: Investigate why gap_scan(None) or active(False) hangs with HCI RX task
        #if 0 && MICROPY_PY_THREAD
        extern void mp_bluetooth_zephyr_hci_rx_task_start(void);
        mp_bluetooth_zephyr_hci_rx_task_start();
        DEBUG_printf("HCI RX task started\n");
        #endif

    } else {
        DEBUG_printf("BLE already ACTIVE (state=%d)\n", mp_bluetooth_zephyr_ble_state);
    }

    mp_bluetooth_zephyr_ble_state = MP_BLUETOOTH_ZEPHYR_BLE_STATE_ACTIVE;

    // Start the HCI polling cycle by triggering the first poll
    // This must be done after state is ACTIVE so mp_bluetooth_hci_poll() will run
    extern void mp_bluetooth_hci_poll_now(void);
    mp_bluetooth_hci_poll_now();

    DEBUG_printf("mp_bluetooth_init: ready\n");

    return 0;
}

int mp_bluetooth_deinit(void) {
    DEBUG_printf("mp_bluetooth_deinit %d\n", mp_bluetooth_zephyr_ble_state);
    if (mp_bluetooth_zephyr_ble_state == MP_BLUETOOTH_ZEPHYR_BLE_STATE_OFF
        || mp_bluetooth_zephyr_ble_state == MP_BLUETOOTH_ZEPHYR_BLE_STATE_SUSPENDED) {
        return 0;
    }

    // NOTE: Do NOT set state to SUSPENDED yet - we need polling to continue
    // working so that HCI commands in bt_le_adv_stop() and bt_disable() can
    // complete. The state will be set to SUSPENDED after bt_disable().

    // Stop advertising/scanning before bt_disable()
    // Note: These may fail during soft reset if stack is in bad state.
    // We ignore errors here to ensure cleanup continues.

    // Clean up pre-allocated connection object
    if (mp_bt_zephyr_next_conn != NULL) {
        DEBUG_printf("mp_bluetooth_deinit: cleaning up pre-allocated connection\n");
        mp_bt_zephyr_next_conn = NULL;
    }

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

    #if CONFIG_BT_GATT_DYNAMIC_DB
    for (size_t i = 0; i < MP_STATE_PORT(bluetooth_zephyr_root_pointers)->n_services; ++i) {
        bt_gatt_service_unregister(MP_STATE_PORT(bluetooth_zephyr_root_pointers)->services[i]);
        MP_STATE_PORT(bluetooth_zephyr_root_pointers)->services[i] = NULL;
    }
    #endif

    // Call bt_disable() to properly shutdown the BLE stack
    // This sends HCI_Reset to the controller and cleans up internal state
    // Without this, bt_enable() returns -EALREADY on reinit and skips initialization
    // Note: bt_disable() may timeout (max 5s) if HCI transport is broken.
    // We continue cleanup regardless of the result.
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

        // Re-initialize the command credit semaphore so bt_enable can start fresh.
        // After a timeout, ncmd_sem may be depleted (0 credits) preventing new commands.
        DEBUG_printf("mp_bluetooth_deinit: reinitializing bt_dev.ncmd_sem\n");
        k_sem_init(&bt_dev.ncmd_sem, 1, 1);
    }

    // Drain any pending work items before stopping threads
    // This ensures connection events and other callbacks are processed
    extern bool mp_bluetooth_zephyr_work_drain(void);
    mp_bluetooth_zephyr_work_drain();

    // Now stop HCI RX task and work thread
    mp_bluetooth_zephyr_hci_rx_task_stop();
    mp_bluetooth_zephyr_work_thread_stop();

    // Deinit port-specific resources (CYW43 cleanup, soft timers, etc.)
    // This must be done after bt_disable() completes.
    #if MICROPY_PY_NETWORK_CYW43 || MICROPY_PY_BLUETOOTH_USE_ZEPHYR_HCI
    extern void mp_bluetooth_zephyr_port_deinit(void);
    mp_bluetooth_zephyr_port_deinit();
    #endif

    MP_STATE_PORT(bluetooth_zephyr_root_pointers) = NULL;
    mp_bt_zephyr_next_conn = NULL;

    // Set state to OFF so next init does full re-initialization
    // (including controller init and callback registration)
    mp_bluetooth_zephyr_ble_state = MP_BLUETOOTH_ZEPHYR_BLE_STATE_OFF;

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

    // Unregister and unref any previous service definitions.
    for (size_t i = 0; i < MP_STATE_PORT(bluetooth_zephyr_root_pointers)->n_services; ++i) {
        bt_gatt_service_unregister(MP_STATE_PORT(bluetooth_zephyr_root_pointers)->services[i]);
        MP_STATE_PORT(bluetooth_zephyr_root_pointers)->services[i] = NULL;
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

    add_service(create_zephyr_uuid(service_uuid), &svc_attributes[attr_index]);
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

        add_characteristic(&add_char, &svc_attributes[attr_index], &svc_attributes[attr_index + 1]);

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

                add_descriptor(curr_char, &add_desc, &svc_attributes[attr_index]);
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
    struct bt_conn_info info;
    bt_conn_get_info(conn, &info);
    uint16_t chr_handle = params->attr->handle - 1;
    mp_bluetooth_gatts_on_indicate_complete(info.id, chr_handle, err);
}

static ssize_t mp_bt_zephyr_gatts_attr_read(struct bt_conn *conn, const struct bt_gatt_attr *attr, void *buf, uint16_t len, uint16_t offset) {
    // we receive the value handle, but to look up in the gatts db we need the characteristic handle, and that is is the value handle minus 1
    uint16_t _handle = attr->handle - 1;

    DEBUG_printf("BLE attr read for handle %d\n", attr->handle);

    mp_bluetooth_gatts_db_entry_t *entry = mp_bluetooth_gatts_db_lookup(MP_STATE_PORT(bluetooth_zephyr_root_pointers)->gatts_db, _handle);
    if (!entry) {
        // it could be a descriptor instead
        _handle = attr->handle;
        entry = mp_bluetooth_gatts_db_lookup(MP_STATE_PORT(bluetooth_zephyr_root_pointers)->gatts_db, _handle);
        if (!entry) {
            return BT_GATT_ERR(BT_ATT_ERR_INVALID_HANDLE);
        }
    }

    return bt_gatt_attr_read(conn, attr, buf, len, offset, entry->data, entry->data_len);
}

static ssize_t mp_bt_zephyr_gatts_attr_write(struct bt_conn *conn, const struct bt_gatt_attr *attr, const void *buf, uint16_t len, uint16_t offset, uint8_t flags) {
    struct bt_conn_info info;
    bt_conn_get_info(conn, &info);

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

    mp_bluetooth_gatts_on_write(info.id, _handle);

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
        struct bt_gatt_attr *attr_val = mp_bt_zephyr_find_attr_by_handle(value_handle + 1);

        if (attr_val) {
            switch (gatts_op) {
                case MP_BLUETOOTH_GATTS_OP_NOTIFY: {
                    err = bt_gatt_notify(connection->conn, attr_val, value, value_len);
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
    mp_raise_OSError(MP_EOPNOTSUPP);
}

int mp_bluetooth_set_preferred_mtu(uint16_t mtu) {
    if (!mp_bluetooth_is_active()) {
        return ERRNO_BLUETOOTH_NOT_ACTIVE;
    }
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
    k_timer_start(&mp_bluetooth_zephyr_gap_scan_timer, K_MSEC(duration_ms), K_NO_WAIT);
    mp_bluetooth_zephyr_gap_scan_state = MP_BLUETOOTH_ZEPHYR_GAP_SCAN_STATE_ACTIVE;
    int err = bt_le_scan_start(&param, NULL);
    DEBUG_printf("gap_scan_start: err=%d\n", err);
    return bt_err_to_errno(err);
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
    if (!mp_bluetooth_is_active()) {
        return ERRNO_BLUETOOTH_NOT_ACTIVE;
    }

    // Stop scanning if active (can't scan and initiate connection simultaneously)
    if (mp_bluetooth_zephyr_gap_scan_state != MP_BLUETOOTH_ZEPHYR_GAP_SCAN_STATE_INACTIVE) {
        mp_bluetooth_gap_scan_stop();
    }

    // Convert MicroPython address (BE byte order) to Zephyr address (LE byte order)
    bt_addr_le_t peer_addr;
    peer_addr.type = addr_type;
    for (int i = 0; i < 6; ++i) {
        peer_addr.a.val[i] = addr[5 - i];  // Reverse byte order: BE -> LE
    }

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

    // Pre-allocate connection tracking structure
    if (mp_bt_zephyr_next_conn != NULL) {
        // This shouldn't happen - indicates previous connection didn't properly clean up
        DEBUG_printf("WARNING: mp_bt_zephyr_next_conn not NULL, resetting before allocation\n");
        mp_bt_zephyr_next_conn = NULL;
    }
    mp_bt_zephyr_next_conn = m_new0(mp_bt_zephyr_conn_t, 1);
    mp_obj_list_append(MP_STATE_PORT(bluetooth_zephyr_root_pointers)->objs_list, MP_OBJ_FROM_PTR(mp_bt_zephyr_next_conn));

    // Initiate connection
    struct bt_conn *conn;
    int err = bt_conn_le_create(&peer_addr, &create_param, &conn_param, &conn);

    if (err != 0) {
        // Connection initiation failed - structure registered with GC, just reset pointer
        DEBUG_printf("  bt_conn_le_create failed: err=%d\n", err);
        mp_bt_zephyr_next_conn = NULL;
        return bt_err_to_errno(err);
    }

    // Store conn handle for cancellation support
    // bt_conn_le_create() returns with a reference - we keep it for cancellation
    // In mp_bt_zephyr_connected callback, we take ANOTHER reference for storage
    DEBUG_printf("  bt_conn_le_create succeeded, conn=%p\n", conn);
    mp_bt_zephyr_next_conn->conn = conn;

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

// Get bt_conn pointer from connection handle, returns NULL if not found.
static struct bt_conn *mp_bt_zephyr_get_conn(uint16_t conn_handle) {
    mp_bt_zephyr_conn_t *connection = mp_bt_zephyr_find_connection(conn_handle);
    return connection ? connection->conn : NULL;
}
#endif // MICROPY_PY_BLUETOOTH_ENABLE_GATT_CLIENT

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

        mp_bluetooth_gattc_on_characteristic_result(conn_handle,
            rp->gattc_pending_char.value_handle,
            end_handle,
            rp->gattc_pending_char.properties,
            &rp->gattc_pending_char.uuid);
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

// Descriptor discovery callback
static uint8_t gattc_descriptor_discover_cb(struct bt_conn *conn,
    const struct bt_gatt_attr *attr, struct bt_gatt_discover_params *params) {

    if (!mp_bluetooth_is_active()) {
        return BT_GATT_ITER_STOP;
    }

    uint16_t conn_handle = MP_STATE_PORT(bluetooth_zephyr_root_pointers)->gattc_discover_conn_handle;

    if (attr == NULL) {
        // Discovery complete
        mp_bluetooth_gattc_on_discover_complete(MP_BLUETOOTH_IRQ_GATTC_DESCRIPTOR_DONE, conn_handle, 0);
        return BT_GATT_ITER_STOP;
    }

    // Report descriptor
    mp_obj_bluetooth_uuid_t desc_uuid = zephyr_uuid_to_mp(attr->uuid);
    mp_bluetooth_gattc_on_descriptor_result(conn_handle, attr->handle, &desc_uuid);

    return BT_GATT_ITER_CONTINUE;
}

// Read callback
static uint8_t gattc_read_cb(struct bt_conn *conn, uint8_t err,
    struct bt_gatt_read_params *params, const void *data, uint16_t length) {

    if (!mp_bluetooth_is_active()) {
        return BT_GATT_ITER_STOP;
    }

    mp_bluetooth_zephyr_root_pointers_t *rp = MP_STATE_PORT(bluetooth_zephyr_root_pointers);
    uint16_t conn_handle = rp->gattc_read_conn_handle;
    uint16_t value_handle = rp->gattc_read_value_handle;

    if (data != NULL && length > 0) {
        // Data available
        const uint8_t *data_ptr = (const uint8_t *)data;
        mp_bluetooth_gattc_on_data_available(MP_BLUETOOTH_IRQ_GATTC_READ_RESULT,
            conn_handle, value_handle, &data_ptr, &length, 1);
    }

    if (data == NULL) {
        // Read complete
        mp_bluetooth_gattc_on_read_write_status(MP_BLUETOOTH_IRQ_GATTC_READ_DONE,
            conn_handle, value_handle, err);
        return BT_GATT_ITER_STOP;
    }

    return BT_GATT_ITER_CONTINUE;
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

// MTU exchange callback
static void gattc_mtu_exchange_cb(struct bt_conn *conn, uint8_t err,
    struct bt_gatt_exchange_params *params) {

    if (!mp_bluetooth_is_active()) {
        return;
    }

    if (err == 0) {
        uint16_t mtu = bt_gatt_get_mtu(conn);
        mp_bluetooth_gatts_on_mtu_exchanged(
            MP_STATE_PORT(bluetooth_zephyr_root_pointers)->gattc_mtu_conn_handle, mtu);
    }
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

    int err = bt_gatt_discover(conn, &rp->gattc_discover_params);
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

    int err = bt_gatt_read(conn, &rp->gattc_read_params);
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
    return bt_err_to_errno(err);
    #else
    (void)conn_handle;
    return MP_EOPNOTSUPP;
    #endif
}

// Pairing/Bonding stubs (Phase 1: No persistent storage)
int mp_bluetooth_gap_pair(uint16_t conn_handle) {
    (void)conn_handle;
    return MP_EOPNOTSUPP; // Phase 1: No pairing support
}

int mp_bluetooth_gap_passkey(uint16_t conn_handle, uint8_t action, mp_int_t passkey) {
    (void)conn_handle; (void)action; (void)passkey;
    return MP_EOPNOTSUPP;
}

void mp_bluetooth_set_bonding(bool enabled) {
    (void)enabled;
    // Phase 1: No bonding (persistent storage) - no-op
}

void mp_bluetooth_set_le_secure(bool enabled) {
    (void)enabled;
    // Phase 1: No secure connections config - no-op
}

void mp_bluetooth_set_mitm_protection(bool enabled) {
    (void)enabled;
    // Phase 1: MITM protection config - no-op
    // TODO: Configure bt_conn_auth_cb or SMP settings
}

void mp_bluetooth_set_io_capability(uint8_t capability) {
    (void)capability;
    // Phase 1: IO capability config - no-op
    // TODO: Configure bt_conn_auth_cb io_capa field
}

MP_REGISTER_ROOT_POINTER(struct _mp_bluetooth_zephyr_root_pointers_t *bluetooth_zephyr_root_pointers);

#endif // MICROPY_PY_BLUETOOTH
