/*
 * Zephyr BLE Feature Stubs
 * Provides stub implementations for advanced BLE features (ISO, DF, CS)
 * These features are disabled in Phase 1
 */

#include <stdint.h>
#include <stddef.h>

// Forward declare net_buf (we don't need the full definition for stubs)
struct net_buf;

// =============================================================================
// ISO (Isochronous Channels) Stubs - Used for LE Audio
// =============================================================================

void hci_iso(struct net_buf *buf) {
    (void)buf;
    // No-op: ISO not implemented
}

void hci_le_cis_established(struct net_buf *buf) {
    (void)buf;
    // No-op: ISO not implemented
}

void hci_le_cis_established_v2(struct net_buf *buf) {
    (void)buf;
    // No-op: ISO not implemented
}

void hci_le_cis_req(struct net_buf *buf) {
    (void)buf;
    // No-op: ISO not implemented
}

void hci_le_big_complete(struct net_buf *buf) {
    (void)buf;
    // No-op: ISO not implemented
}

void hci_le_big_terminate(struct net_buf *buf) {
    (void)buf;
    // No-op: ISO not implemented
}

void hci_le_big_sync_established(struct net_buf *buf) {
    (void)buf;
    // No-op: ISO not implemented
}

void hci_le_big_sync_lost(struct net_buf *buf) {
    (void)buf;
    // No-op: ISO not implemented
}

void bt_hci_le_biginfo_adv_report(struct net_buf *buf) {
    (void)buf;
    // No-op: ISO not implemented
}

// =============================================================================
// Direction Finding (DF) Stubs
// =============================================================================

int le_df_init(void) {
    // No-op: Direction Finding not implemented
    return 0;
}

// =============================================================================
// Channel Sounding (CS) Stubs - BLE 5.4 distance measurement
// =============================================================================

void bt_hci_le_cs_read_remote_supported_capabilities_complete(struct net_buf *buf) {
    (void)buf;
    // No-op: Channel Sounding not implemented
}

void bt_hci_le_cs_read_remote_fae_table_complete(struct net_buf *buf) {
    (void)buf;
    // No-op: Channel Sounding not implemented
}

void bt_hci_le_cs_subevent_result_continue(struct net_buf *buf) {
    (void)buf;
    // No-op: Channel Sounding not implemented
}

void bt_hci_le_cs_test_end_complete(struct net_buf *buf) {
    (void)buf;
    // No-op: Channel Sounding not implemented
}

void bt_hci_le_cs_procedure_enable_complete(struct net_buf *buf) {
    (void)buf;
    // No-op: Channel Sounding not implemented
}

void bt_hci_le_cs_subevent_result(struct net_buf *buf) {
    (void)buf;
    // No-op: Channel Sounding not implemented
}

void bt_hci_le_cs_config_complete_event(struct net_buf *buf) {
    (void)buf;
    // No-op: Channel Sounding not implemented
}

void bt_hci_le_cs_security_enable_complete(struct net_buf *buf) {
    (void)buf;
    // No-op: Channel Sounding not implemented
}

// ISO array stub - needed by conn.c
// In Zephyr, this is an array of ISO connection structures
// We define it as NULL array since ISO is disabled
struct bt_conn *iso_conns = NULL;

// ISO disconnection handler stub
void bt_iso_disconnected(struct bt_conn *conn) {
    (void)conn;
    // No-op: ISO not implemented
}

// ISO cleanup stub - called from conn.c deferred_work()
void bt_iso_cleanup_acl(struct bt_conn *conn) {
    (void)conn;
    // No-op: ISO not implemented
}

// ISO channel state change stub
void bt_iso_chan_set_state(void *chan, int state) {
    (void)chan;
    (void)state;
    // No-op: ISO not implemented
}
