/*
 * Zephyr BLE Settings
 *
 * Routes bond key storage through MicroPython's _IRQ_GET_SECRET / _IRQ_SET_SECRET
 * Python callback interface (same as NimBLE). Other settings types (CCC, CF, hash)
 * remain as no-op stubs.
 *
 * CONFIG_BT_SETTINGS is defined to 0 in zephyr_ble_config.h. Because keys.c uses
 * `#if defined(CONFIG_BT_SETTINGS)` (not IS_ENABLED), bt_keys_store() and
 * bt_keys_clear() call our typed wrappers. The IS_ENABLED paths (bt_settings_init,
 * etc.) remain disabled.
 */

#include <string.h>
#include <errno.h>

#include <zephyr/bluetooth/bluetooth.h>
#include <zephyr/bluetooth/conn.h>
#include "../../lib/zephyr/subsys/bluetooth/host/conn_internal.h"
#include "../../lib/zephyr/subsys/bluetooth/host/keys.h"

#include "py/mpconfig.h"
#include "py/runtime.h"

#if MICROPY_PY_BLUETOOTH_ENABLE_PAIRING_BONDING
#include "extmod/modbluetooth.h"
#endif

#include "extmod/zephyr_ble/hal/zephyr_ble_settings.h"

#if ZEPHYR_BLE_DEBUG
#define DEBUG_printf(...) mp_printf(&mp_plat_print, "BLE: " __VA_ARGS__)
#else
#define DEBUG_printf(...) do {} while (0)
#endif

// Set to 1 to stub out bond key storage for isolation testing (Step 3)
#ifndef ZEPHYR_BLE_SETTINGS_NOOP
#define ZEPHYR_BLE_SETTINGS_NOOP 0
#endif

// ---- Bond key storage (active when pairing/bonding enabled) ----

#if MICROPY_PY_BLUETOOTH_ENABLE_PAIRING_BONDING

int bt_settings_store_keys(uint8_t id, const bt_addr_le_t *addr,
    const void *value, size_t val_len) {
    #if ZEPHYR_BLE_SETTINGS_NOOP
    DEBUG_printf("<<< bt_settings_store_keys: NO-OP stub (isolation test)\n");
    return 0;
    #endif

    // Build self-contained value blob: addr(7) + id(1) + keys_data(N)
    uint8_t buf[sizeof(bt_addr_le_t) + 1 + BT_KEYS_STORAGE_LEN];
    size_t total = sizeof(bt_addr_le_t) + 1 + val_len;
    if (total > sizeof(buf)) {
        DEBUG_printf("<<< bt_settings_store_keys: EINVAL total=%d > buf=%d\n",
            (int)total, (int)sizeof(buf));
        return -EINVAL;
    }
    memcpy(buf, addr, sizeof(bt_addr_le_t));
    buf[sizeof(bt_addr_le_t)] = id;
    memcpy(buf + sizeof(bt_addr_le_t) + 1, value, val_len);

    bool result = mp_bluetooth_gap_on_set_secret(
        MP_BLUETOOTH_ZEPHYR_SECRET_KEYS,
        (const uint8_t *)addr, sizeof(bt_addr_le_t),
        buf, total);
    DEBUG_printf("<<< bt_settings_store_keys: set_secret returned %d\n", result);

    if (!result) {
        return -EIO;
    }
    return 0;
}

int bt_settings_delete_keys(uint8_t id, const bt_addr_le_t *addr) {
    DEBUG_printf(">>> bt_settings_delete_keys: addr=%02x:%02x:%02x:%02x:%02x:%02x id=%d\n",
        addr->a.val[5], addr->a.val[4], addr->a.val[3],
        addr->a.val[2], addr->a.val[1], addr->a.val[0], id);

    #if ZEPHYR_BLE_SETTINGS_NOOP
    DEBUG_printf("<<< bt_settings_delete_keys: NO-OP stub (isolation test)\n");
    return 0;
    #endif

    // Note: id is not included in the lookup key (only addr is used).
    // MicroPython only uses BT_ID_DEFAULT (0), so this is fine.
    // Multi-identity support would require including id in the key.
    (void)id;
    bool result = mp_bluetooth_gap_on_set_secret(
        MP_BLUETOOTH_ZEPHYR_SECRET_KEYS,
        (const uint8_t *)addr, sizeof(bt_addr_le_t),
        NULL, 0);
    DEBUG_printf("<<< bt_settings_delete_keys: set_secret returned %d\n", result);

    if (!result) {
        return -EIO;
    }
    return 0;
}

void mp_bluetooth_zephyr_load_keys(void) {
    #if ZEPHYR_BLE_SETTINGS_NOOP
    return;
    #endif

    const uint8_t *value;
    size_t value_len;
    int loaded = 0;

    for (uint8_t idx = 0; idx < CONFIG_BT_MAX_PAIRED; idx++) {
        if (!mp_bluetooth_gap_on_get_secret(
                MP_BLUETOOTH_ZEPHYR_SECRET_KEYS, idx,
                NULL, 0,  // NULL key = enumerate by index
                &value, &value_len)) {
            break;  // No more entries
        }

        size_t min_len = sizeof(bt_addr_le_t) + 1;
        if (value_len < min_len) {
            continue;  // Corrupted entry, skip
        }

        // Parse self-contained blob: addr(7) + id(1) + keys_data(N)
        const bt_addr_le_t *addr = (const bt_addr_le_t *)value;
        uint8_t id = value[sizeof(bt_addr_le_t)];
        const void *keys_data = value + sizeof(bt_addr_le_t) + 1;
        size_t keys_data_len = value_len - min_len;

        // Allocate key slot and populate from stored data
        struct bt_keys *keys = bt_keys_get_addr(id, addr);
        if (keys && keys_data_len <= BT_KEYS_STORAGE_LEN) {
            memcpy(keys->storage_start, keys_data, keys_data_len);
            loaded++;
        }
    }
}

#else // !MICROPY_PY_BLUETOOTH_ENABLE_PAIRING_BONDING

// No-op stubs when pairing/bonding is disabled
int bt_settings_store_keys(uint8_t id, const bt_addr_le_t *addr,
    const void *value, size_t val_len) {
    (void)id;
    (void)addr;
    (void)value;
    (void)val_len;
    return 0;
}

int bt_settings_delete_keys(uint8_t id, const bt_addr_le_t *addr) {
    (void)id;
    (void)addr;
    return 0;
}

#endif // MICROPY_PY_BLUETOOTH_ENABLE_PAIRING_BONDING

// ---- Remaining stubs (no-op, correct signatures) ----

int bt_settings_store_ccc(uint8_t id, const bt_addr_le_t *addr,
    const void *value, size_t val_len) {
    (void)id;
    (void)addr;
    (void)value;
    (void)val_len;
    return 0;
}

int bt_settings_store_cf(uint8_t id, const bt_addr_le_t *addr,
    const void *value, size_t val_len) {
    (void)id;
    (void)addr;
    (void)value;
    (void)val_len;
    return 0;
}

int bt_settings_store_hash(const void *value, size_t val_len) {
    (void)value;
    (void)val_len;
    return 0;
}

int bt_settings_decode_key(const char *key, bt_addr_le_t *addr) {
    (void)key;
    (void)addr;
    return -1;
}

int settings_name_next(const char *name, const char **next) {
    (void)name;
    (void)next;
    return 0;
}

int bt_settings_delete_ccc(uint8_t id, const bt_addr_le_t *addr) {
    (void)id;
    (void)addr;
    return 0;
}

int bt_settings_delete_cf(uint8_t id, const bt_addr_le_t *addr) {
    (void)id;
    (void)addr;
    return 0;
}
