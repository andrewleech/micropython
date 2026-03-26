/*
 * This file is part of the MicroPython project, http://micropython.org/
 *
 * SPDX-License-Identifier: Apache-2.0
 *
 * Minimal stub for zephyr/logging/log.h
 *
 * Shadows the real Zephyr logging header to prevent it from pulling in
 * the full Zephyr arch header chain (cbprintf -> arch/cpu.h -> arch/x86/).
 * BLE debug output uses MicroPython's mp_printf instead of Zephyr logging.
 */

#ifndef MP_ZEPHYR_LOGGING_LOG_STUB_H_
#define MP_ZEPHYR_LOGGING_LOG_STUB_H_

// Block the real header from being included.
#define ZEPHYR_INCLUDE_LOGGING_LOG_H_

// Logging macros used by Zephyr BLE host and net_buf -- all no-ops.
#define LOG_MODULE_REGISTER(...)
#define LOG_MODULE_DECLARE(...)
#define LOG_LEVEL_SET(level)

#define LOG_DBG(...) do {} while (0)
#define LOG_INF(...) do {} while (0)
#define LOG_WRN(...) do {} while (0)
#define LOG_ERR(...) do {} while (0)

#define LOG_HEXDUMP_DBG(...) do {} while (0)
#define LOG_HEXDUMP_INF(...) do {} while (0)
#define LOG_HEXDUMP_WRN(...) do {} while (0)
#define LOG_HEXDUMP_ERR(...) do {} while (0)

// Log levels (referenced in some Zephyr code).
#define LOG_LEVEL_NONE 0
#define LOG_LEVEL_ERR  1
#define LOG_LEVEL_WRN  2
#define LOG_LEVEL_INF  3
#define LOG_LEVEL_DBG  4

#endif /* MP_ZEPHYR_LOGGING_LOG_STUB_H_ */
