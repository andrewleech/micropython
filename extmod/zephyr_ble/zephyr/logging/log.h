/*
 * Zephyr logging/log.h wrapper for MicroPython
 * Stubs out Zephyr logging system (CONFIG_LOG=0 in autoconf.h)
 */

#ifndef ZEPHYR_LOGGING_LOG_H_
#define ZEPHYR_LOGGING_LOG_H_

// Logging is disabled (CONFIG_LOG=0)
// All logging macros expand to no-ops

// Log level macros (not used when logging disabled)
#define LOG_LEVEL_NONE 0
#define LOG_LEVEL_ERR  1
#define LOG_LEVEL_WRN  2
#define LOG_LEVEL_INF  3
#define LOG_LEVEL_DBG  4

// Module registration (no-op)
#define LOG_MODULE_REGISTER(name, ...) \
    static const int _log_unused_##name __attribute__((unused)) = 0

// Logging macros (all no-ops)
#define LOG_ERR(...)   do {} while (0)
#define LOG_WRN(...)   do {} while (0)
#define LOG_INF(...)   do {} while (0)
#define LOG_DBG(...)   do {} while (0)

// Hexdump logging (no-op)
#define LOG_HEXDUMP_ERR(data, len, ...) do {} while (0)
#define LOG_HEXDUMP_WRN(data, len, ...) do {} while (0)
#define LOG_HEXDUMP_INF(data, len, ...) do {} while (0)
#define LOG_HEXDUMP_DBG(data, len, ...) do {} while (0)

// If we want logging in the future, could map to printf:
// #define LOG_ERR(...) printf("ERR: " __VA_ARGS__)

#endif /* ZEPHYR_LOGGING_LOG_H_ */
