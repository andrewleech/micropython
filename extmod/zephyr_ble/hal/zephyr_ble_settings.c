/*
 * Zephyr BLE Settings Stubs
 * Stub implementations for settings storage functions (disabled in Phase 1)
 */

#include <zephyr/bluetooth/bluetooth.h>
#include <zephyr/bluetooth/conn.h>
#include "../../lib/zephyr/subsys/bluetooth/host/conn_internal.h"

// Stub: Store CCC (Client Characteristic Configuration) to settings
int bt_settings_store_ccc(uint8_t id, const bt_addr_le_t *peer) {
    (void)id;
    (void)peer;
    // Phase 1: RAM-only, no persistent storage
    return 0;
}

// Stub: Store CF (Client Features) to settings
int bt_settings_store_cf(uint8_t id, const bt_addr_le_t *peer) {
    (void)id;
    (void)peer;
    // Phase 1: RAM-only, no persistent storage
    return 0;
}

// Stub: Store database hash to settings
int bt_settings_store_hash(void) {
    // Phase 1: RAM-only, no persistent storage
    return 0;
}

// Stub: Store bonding keys to settings
int bt_settings_store_keys(const bt_addr_le_t *peer, uint8_t id) {
    (void)peer;
    (void)id;
    // Phase 1: RAM-only, no persistent key storage
    return 0;
}

// Stub: Decode settings key
int bt_settings_decode_key(const char *key, bt_addr_le_t *addr) {
    (void)key;
    (void)addr;
    // Phase 1: No settings support
    return -1;  // Error - key not found
}

// Stub: Get next component of settings name
int settings_name_next(const char *name, const char **next) {
    (void)name;
    (void)next;
    // Phase 1: No settings support
    return 0;  // No more components
}

// Stub: Delete CCC settings
int bt_settings_delete_ccc(uint8_t id, const bt_addr_le_t *addr) {
    (void)id;
    (void)addr;
    return 0;
}

// Stub: Delete CF settings
int bt_settings_delete_cf(uint8_t id, const bt_addr_le_t *addr) {
    (void)id;
    (void)addr;
    return 0;
}
