/*
 * Wrapper for Zephyr gatt.c that exposes static internals for MicroPython.
 *
 * gatt.c's subscriptions[] array is static, making it inaccessible from
 * outside the translation unit. We compile gatt.c as part of this TU so
 * we can provide a memset-based reset function without patching the
 * Zephyr submodule.
 *
 * This file REPLACES gatt.c in the build — do not compile both.
 */

// Redirect malloc/free to scoped allocator when GATT pool is enabled.
// Zephyr's gatt.c calls malloc() for attribute storage; these redirects
// ensure it hits our bump allocator instead of polluting the global symbol.
#if MICROPY_BLUETOOTH_ZEPHYR_GATT_POOL
#include <stddef.h>
extern void *zephyr_ble_gatt_malloc(size_t size);
extern void zephyr_ble_gatt_free(void *ptr);
#define malloc zephyr_ble_gatt_malloc
#define free zephyr_ble_gatt_free
#endif

#include "lib/zephyr/subsys/bluetooth/host/gatt.c"

#if MICROPY_BLUETOOTH_ZEPHYR_GATT_POOL
#undef malloc
#undef free
#endif

#if defined(CONFIG_BT_GATT_CLIENT)
// Reset all GATT client subscriptions by zeroing the static array.
// Called at init as a safety net: if the previous bt_disable() failed
// (CYW43 SPI hang, IPCC timeout), subscriptions[] retains stale
// bt_gatt_subscribe_params pointers into freed GC heap memory.
// A subsequent disconnect would call params->notify() through a
// dangling pointer, causing a HardFault.
//
// This is safe because:
// - Called before bt_enable(), so no active connections or subscriptions
// - Zeroed peer address (BT_ADDR_LE_ANY) marks all entries as free
// - sys_slist_t zeroed = empty list (head = NULL)
void bt_gatt_reset_subscriptions(void) {
    memset(subscriptions, 0, sizeof(subscriptions));
}
#endif
