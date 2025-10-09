/*
 * Zephyr logging wrapper for MicroPython
 * Maps Zephyr's logging API to printf or no-op
 */

#ifndef ZEPHYR_LOGGING_LOG_H_
#define ZEPHYR_LOGGING_LOG_H_

#include <stdio.h>

// Logging levels
#define LOG_LEVEL_NONE 0
#define LOG_LEVEL_ERR  1
#define LOG_LEVEL_WRN  2
#define LOG_LEVEL_INF  3
#define LOG_LEVEL_DBG  4

// Module registration (no-op in MicroPython)
#define LOG_MODULE_REGISTER(module_name, level) \
    static const int _log_level = level; \
    (void)_log_level

#define LOG_MODULE_DECLARE(module_name, level) \
    static const int _log_level = level; \
    (void)_log_level

// Determine if we should enable logging
#if defined(MICROPY_DEBUG_VERBOSE) && MICROPY_DEBUG_VERBOSE
#define BT_LOG_ENABLED 1
#else
#define BT_LOG_ENABLED 0
#endif

// Logging macros
#if BT_LOG_ENABLED

#define LOG_ERR(fmt, ...) printf("ERR: " fmt "\n",##__VA_ARGS__)
#define LOG_WRN(fmt, ...) printf("WRN: " fmt "\n",##__VA_ARGS__)
#define LOG_INF(fmt, ...) printf("INF: " fmt "\n",##__VA_ARGS__)
#define LOG_DBG(fmt, ...) printf("DBG: " fmt "\n",##__VA_ARGS__)

#define LOG_HEXDUMP_ERR(data, len, fmt, ...) \
    do { \
        printf("ERR: " fmt "\n",##__VA_ARGS__); \
        for (size_t i = 0; i < len; i++) { \
            printf("%02x ", ((uint8_t *)(data))[i]); \
        } \
        printf("\n"); \
    } while (0)

#define LOG_HEXDUMP_WRN(data, len, fmt, ...) \
    do { \
        printf("WRN: " fmt "\n",##__VA_ARGS__); \
        for (size_t i = 0; i < len; i++) { \
            printf("%02x ", ((uint8_t *)(data))[i]); \
        } \
        printf("\n"); \
    } while (0)

#define LOG_HEXDUMP_INF(data, len, fmt, ...) \
    do { \
        printf("INF: " fmt "\n",##__VA_ARGS__); \
        for (size_t i = 0; i < len; i++) { \
            printf("%02x ", ((uint8_t *)(data))[i]); \
        } \
        printf("\n"); \
    } while (0)

#define LOG_HEXDUMP_DBG(data, len, fmt, ...) \
    do { \
        printf("DBG: " fmt "\n",##__VA_ARGS__); \
        for (size_t i = 0; i < len; i++) { \
            printf("%02x ", ((uint8_t *)(data))[i]); \
        } \
        printf("\n"); \
    } while (0)

#else

// No-op logging when disabled
#define LOG_ERR(fmt, ...) do { } while (0)
#define LOG_WRN(fmt, ...) do { } while (0)
#define LOG_INF(fmt, ...) do { } while (0)
#define LOG_DBG(fmt, ...) do { } while (0)

#define LOG_HEXDUMP_ERR(data, len, fmt, ...) do { } while (0)
#define LOG_HEXDUMP_WRN(data, len, fmt, ...) do { } while (0)
#define LOG_HEXDUMP_INF(data, len, fmt, ...) do { } while (0)
#define LOG_HEXDUMP_DBG(data, len, fmt, ...) do { } while (0)

#endif

#endif /* ZEPHYR_LOGGING_LOG_H_ */
