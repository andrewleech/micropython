/*
 * Zephyr BLE Monitor Stubs
 * Provides stub implementations for Bluetooth monitor/debugging functions
 */

#include <stdint.h>
#include <stddef.h>

// Bluetooth monitor functions - used for sniffing/debugging HCI traffic
// In Phase 1, these are no-ops (no monitor support)

void bt_monitor_send(uint8_t opcode, const void *data, size_t len) {
    (void)opcode;
    (void)data;
    (void)len;
    // No-op: Monitor not implemented
}

void bt_monitor_new_index(uint8_t type, uint8_t bus, const void *addr,
                           size_t addr_len, const char *name) {
    (void)type;
    (void)bus;
    (void)addr;
    (void)addr_len;
    (void)name;
    // No-op: Monitor not implemented
}
