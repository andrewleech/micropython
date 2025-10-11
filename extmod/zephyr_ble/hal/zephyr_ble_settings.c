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
