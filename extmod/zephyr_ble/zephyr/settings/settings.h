/*
 * Zephyr settings/settings.h wrapper for MicroPython
 * Settings/storage API stubs (CONFIG_BT_SETTINGS=0)
 */

#ifndef ZEPHYR_SETTINGS_SETTINGS_H_
#define ZEPHYR_SETTINGS_SETTINGS_H_

// Settings are disabled (CONFIG_BT_SETTINGS=0 in autoconf.h)
// All functions are no-ops

#define SETTINGS_NAME_SEPARATOR '/'

// Settings handler structure (not used)
struct settings_handler {
    const char *name;
    int (*h_get)(const char *key, char *val, int val_len_max);
    int (*h_set)(const char *key, size_t len, void *read_cb, void *cb_arg);
    int (*h_commit)(void);
    int (*h_export)(int (*export_func)(const char *name, const void *val, size_t val_len));
};

// Settings API (all no-ops)
static inline int settings_subsys_init(void) {
    return 0;
}

static inline int settings_register(struct settings_handler *handler) {
    (void)handler;
    return 0;
}

static inline int settings_load(void) {
    return 0;
}

static inline int settings_save_one(const char *name, const void *value, size_t val_len) {
    (void)name;
    (void)value;
    (void)val_len;
    return 0;
}

static inline int settings_delete(const char *name) {
    (void)name;
    return 0;
}

#endif /* ZEPHYR_SETTINGS_SETTINGS_H_ */
