/*
 * Wrapper for Zephyr conn.c that exposes static internals for MicroPython.
 *
 * conn.c's acl_conns[] array is static, making it inaccessible from
 * outside the translation unit. We compile conn.c as part of this TU so
 * we can provide a connection pool reset function without patching the
 * Zephyr submodule.
 *
 * This file REPLACES conn.c in the build — do not compile both.
 */

#include "lib/zephyr/subsys/bluetooth/host/conn.c"

// Force-reset all ACL connection pool refcounts to zero.
// Called at deinit as a safety net after work_drain: if bt_disable()
// failed to fully clean up (e.g. deferred_work didn't run for all
// connections), stale refcounts prevent connection pool slots from
// being reused. This causes EINVAL on the next gap_connect().
//
// This is safe because:
// - Called after bt_disable() and work_drain, so no active BLE traffic
// - Only resets refcounts, doesn't touch connection state machine
void bt_conn_pool_reset(void) {
    for (size_t i = 0; i < ARRAY_SIZE(acl_conns); i++) {
        struct bt_conn *conn = &acl_conns[i];
        atomic_set(&conn->ref, 0);
    }
}
