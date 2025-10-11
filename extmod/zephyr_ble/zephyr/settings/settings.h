/*
 * Zephyr settings/settings.h wrapper for MicroPython
 * Settings subsystem stubs (CONFIG_BT_SETTINGS=0)
 */

#ifndef ZEPHYR_SETTINGS_SETTINGS_H_
#define ZEPHYR_SETTINGS_SETTINGS_H_

#include <stddef.h>
#include <stdbool.h>

// Settings handler definition macro (no-op when settings disabled)
#define SETTINGS_STATIC_HANDLER_DEFINE_WITH_CPRIO(_hname, _tree, _get, _set, _commit, _export, _prio) \
    /* No-op: settings disabled */

// Settings name parsing (stub implementations)
static inline int settings_name_steq(const char *name, const char *key, const char **next) {
    (void)name;
    (void)key;
    (void)next;
    return 0;  // Never matches
}

static inline int settings_name_next(const char *name, const char **next) {
    (void)name;
    (void)next;
    return 0;  // No more components
}

// Settings load callback type (matching Zephyr's signature)
typedef int (*settings_read_cb)(void *cb_arg, void *data, size_t len);

// Settings load direct callback type
typedef int (*settings_load_direct_cb)(const char *key, size_t len,
                                       settings_read_cb read_cb,
                                       void *cb_arg, void *param);

// Settings load function (stub)
static inline int settings_load_subtree_direct(const char *subtree,
                                                settings_load_direct_cb cb,
                                                void *param) {
    (void)subtree;
    (void)cb;
    (void)param;
    return 0;  // No settings to load
}

#endif /* ZEPHYR_SETTINGS_SETTINGS_H_ */
