/*
 * This file is part of the MicroPython project, http://micropython.org/
 *
 * The MIT License (MIT)
 *
 * Copyright (c) 2025 MicroPython Contributors
 *
 * Permission is hereby granted, free of charge, to any person obtaining a copy
 * of this software and associated documentation files (the "Software"), to deal
 * in the Software without restriction, including without limitation the rights
 * to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
 * copies of the Software, and to permit persons to whom the Software is
 * furnished to do so, subject to the following conditions:
 *
 * The above copyright notice and this permission notice shall be included in
 * all copies or substantial portions of the Software.
 *
 * THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
 * IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
 * FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
 * AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
 * LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
 * OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
 * THE SOFTWARE.
 */

#ifndef MICROPY_INCLUDED_EXTMOD_ZEPHYR_BLE_ZEPHYR_BLE_CONFIG_H
#define MICROPY_INCLUDED_EXTMOD_ZEPHYR_BLE_ZEPHYR_BLE_CONFIG_H

// Zephyr BLE Configuration
// Maps Zephyr Kconfig options to static defines for MicroPython

// --- Core BLE Stack ---
#define CONFIG_BT 1
#define CONFIG_BT_HCI_HOST 1
#define CONFIG_BT_MAX_CONN 4

// --- GAP Roles ---
#define CONFIG_BT_BROADCASTER 1
#define CONFIG_BT_OBSERVER 1
#define CONFIG_BT_PERIPHERAL 1
#define CONFIG_BT_CENTRAL 1

// --- Connection Support ---
#define CONFIG_BT_CONN 1
#define CONFIG_BT_MAX_PAIRED 4

// --- GATT Support ---
#define CONFIG_BT_GATT_CLIENT 1
#define CONFIG_BT_GATT_DYNAMIC_DB 1
#define CONFIG_BT_ATT_PREPARE_COUNT 0

// --- Security (SMP) - Initially disabled, will enable in Phase 4 ---
#define CONFIG_BT_SMP 0
#define CONFIG_BT_SIGNING 0
#define CONFIG_BT_SMP_SC_PAIR_ONLY 0
#define CONFIG_BT_PRIVACY 0

// --- L2CAP ---
#define CONFIG_BT_L2CAP_TX_BUF_COUNT 4

// --- Buffer Configuration ---
// ACL buffers
#define CONFIG_BT_BUF_ACL_TX_COUNT 8
#define CONFIG_BT_BUF_ACL_TX_SIZE 27
#define CONFIG_BT_BUF_ACL_RX_COUNT 8
#define CONFIG_BT_BUF_ACL_RX_SIZE 27
#define CONFIG_BT_BUF_ACL_RX_COUNT_EXTRA CONFIG_BT_MAX_CONN

// Event buffers
#define CONFIG_BT_BUF_EVT_RX_COUNT 16
#define CONFIG_BT_BUF_EVT_RX_SIZE 68
#define CONFIG_BT_BUF_EVT_DISCARDABLE_COUNT 3
#define CONFIG_BT_BUF_EVT_DISCARDABLE_SIZE 43

// Command buffers
#define CONFIG_BT_BUF_CMD_TX_COUNT 4
#define CONFIG_BT_BUF_CMD_TX_SIZE 68

// Flow control (disabled for simplicity in MicroPython)
#define CONFIG_BT_HCI_ACL_FLOW_CONTROL 0

// --- Advanced Features - Disabled for Phase 1 ---
#define CONFIG_BT_ISO 0
#define CONFIG_BT_ISO_BROADCASTER 0
#define CONFIG_BT_ISO_SYNC_RECEIVER 0
#define CONFIG_BT_ISO_UNICAST 0
#define CONFIG_BT_DF 0
#define CONFIG_BT_CHANNEL_SOUNDING 0
#define CONFIG_BT_EXT_ADV 0

// --- Host Features ---
#define CONFIG_BT_FILTER_ACCEPT_LIST 1
#define CONFIG_BT_WHITELIST CONFIG_BT_FILTER_ACCEPT_LIST
#define CONFIG_BT_REMOTE_VERSION 1
#define CONFIG_BT_PHY_UPDATE 0
#define CONFIG_BT_DATA_LEN_UPDATE 0

// --- Crypto ---
#define CONFIG_BT_HOST_CRYPTO 0  // Will enable with SMP in Phase 4
#define CONFIG_BT_ECC 0

// --- Settings/Storage - Disabled for Phase 1 (RAM-only) ---
#define CONFIG_BT_SETTINGS 0

// --- Logging/Debug ---
#ifndef CONFIG_BT_DEBUG
#define CONFIG_BT_DEBUG 0
#endif
#define CONFIG_BT_DEBUG_LOG 0
#define CONFIG_BT_MONITOR 0
#define CONFIG_BT_HCI_RAW 0

// --- Shell/Testing - Disabled ---
#define CONFIG_BT_SHELL 0
#define CONFIG_BT_TESTING 0

// --- Classic Bluetooth - Disabled (LE only) ---
#define CONFIG_BT_CLASSIC 0
#define CONFIG_BT_BREDR 0

// --- Long Work Queue ---
#define CONFIG_BT_LONG_WQ 0

// --- Device Name ---
#ifndef CONFIG_BT_DEVICE_NAME
#define CONFIG_BT_DEVICE_NAME "MicroPython"
#endif
#define CONFIG_BT_DEVICE_NAME_MAX 32
#define CONFIG_BT_DEVICE_NAME_DYNAMIC 1
#define CONFIG_BT_DEVICE_NAME_GATT_WRITABLE 1

// --- ID Management ---
#define CONFIG_BT_ID_MAX 1

// --- Advertising ---
#define CONFIG_BT_ADV_DATA_LEN_MAX 31
#define CONFIG_BT_SCAN_RSP_DATA_LEN_MAX 31

// --- ATT/GATT Timeouts ---
#define CONFIG_BT_ATT_TX_COUNT 4

// --- Zephyr System Config ---
#define CONFIG_LITTLE_ENDIAN 1
#define CONFIG_BT_HCI_VS 0
#define CONFIG_BT_HCI_VS_EXT 0

// --- Assert Configuration ---
#ifdef NDEBUG
#define CONFIG_ASSERT 0
#else
#define CONFIG_ASSERT 1
#endif

#endif // MICROPY_INCLUDED_EXTMOD_ZEPHYR_BLE_ZEPHYR_BLE_CONFIG_H
