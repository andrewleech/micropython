/*
 * This file is part of the MicroPython project, http://micropython.org/
 *
 * The MIT License (MIT)
 *
 * Copyright (c) 2025 MicroPython Contributors
 *
 * Stub header for Zephyr BLE common/bt_str.h
 * These are debugging/logging functions - unused when CONFIG_BT_DEBUG=0
 */

#ifndef MICROPY_INCLUDED_EXTMOD_ZEPHYR_BLE_COMMON_BT_STR_H
#define MICROPY_INCLUDED_EXTMOD_ZEPHYR_BLE_COMMON_BT_STR_H

#include <stddef.h>
#include <stdint.h>

// Stub implementations for debug string functions
// These are only used in LOG_DBG() calls which are disabled when CONFIG_BT_DEBUG=0

static inline const char *bt_hex(const void *buf, size_t len) {
    (void)buf;
    (void)len;
    return "";
}

static inline const char *bt_addr_str(const void *addr) {
    (void)addr;
    return "";
}

static inline const char *bt_uuid_str(const void *uuid) {
    (void)uuid;
    return "";
}

#endif // MICROPY_INCLUDED_EXTMOD_ZEPHYR_BLE_COMMON_BT_STR_H
