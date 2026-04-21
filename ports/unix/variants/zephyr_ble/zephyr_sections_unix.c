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

// Zephyr iterable section symbols for Unix port.
//
// On embedded ports, the linker script collects STRUCT_SECTION_ITERABLE
// objects into contiguous arrays bounded by _<type>_list_start/_list_end.
// On Unix we provide paired symbols at the same address so iteration
// loops (start == end) execute zero times.

#include "py/mpconfig.h"

#if MICROPY_PY_BLUETOOTH && MICROPY_BLUETOOTH_ZEPHYR

// net_buf_pool start/end symbols are provided by zephyr_sections.ld
// which collects ._net_buf_pool.static.* sections into a contiguous array.
//
// The remaining iterable types use the manual pool registry
// (net_buf_pool_registry.c) instead of linker sections, but Zephyr
// host code still references the boundary symbols.  Provide empty
// pairs so the iteration loops execute zero times.

__asm__ (
    ".global _bt_conn_cb_list_start\n"
    ".global _bt_conn_cb_list_end\n"
    ".set _bt_conn_cb_list_start, _zephyr_empty_sections\n"
    ".set _bt_conn_cb_list_end, _zephyr_empty_sections\n"
    ".global _bt_gatt_service_static_list_start\n"
    ".global _bt_gatt_service_static_list_end\n"
    ".set _bt_gatt_service_static_list_start, _zephyr_empty_sections\n"
    ".set _bt_gatt_service_static_list_end, _zephyr_empty_sections\n"
    ".global _bt_l2cap_fixed_chan_list_start\n"
    ".global _bt_l2cap_fixed_chan_list_end\n"
    ".set _bt_l2cap_fixed_chan_list_start, _zephyr_empty_sections\n"
    ".set _bt_l2cap_fixed_chan_list_end, _zephyr_empty_sections\n"
    ".global _settings_handler_static_list_start\n"
    ".global _settings_handler_static_list_end\n"
    ".set _settings_handler_static_list_start, _zephyr_empty_sections\n"
    ".set _settings_handler_static_list_end, _zephyr_empty_sections\n"
    ".data\n"
    ".align 8\n"
    "_zephyr_empty_sections: .byte 0\n"
    );

#endif // MICROPY_PY_BLUETOOTH && MICROPY_BLUETOOTH_ZEPHYR
