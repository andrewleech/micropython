/*
 * This file is part of the MicroPython project, http://micropython.org/
 *
 * The MIT License (MIT)
 *
 * Copyright (c) 2026 Andrew Leech
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

#ifndef MICROPY_INCLUDED_SHARED_MBOOT_MBOOT_CONFIG_MBOOT_CONFIG_H
#define MICROPY_INCLUDED_SHARED_MBOOT_MBOOT_CONFIG_MBOOT_CONFIG_H

// bootutil configuration. Everything that depends on the board is in mboot_layout.h.

#include "mboot_layout.h"
#include "mboot_port.h"

#define MCUBOOT_LOG_LEVEL MBOOT_LOG_LEVEL

#define MCUBOOT_WATCHDOG_FEED() mboot_port_wdt_feed()
#define MCUBOOT_CPU_IDLE() do {} while (0)

// The port backend of the security counter can run out of increments (fuses) and can be
// locked after the update; the optional port functions report both.
#if defined(MBOOT_SECCNT_PORT)
#define MCUBOOT_HW_ROLLBACK_PROT_COUNTER_LIMITED 1
#define MCUBOOT_HW_ROLLBACK_PROT_LOCK 1
#endif

#endif // MICROPY_INCLUDED_SHARED_MBOOT_MBOOT_CONFIG_MBOOT_CONFIG_H
