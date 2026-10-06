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

#ifndef MICROPY_INCLUDED_SHARED_MCUBOOT_MCUBOOT_LOG_H
#define MICROPY_INCLUDED_SHARED_MCUBOOT_MCUBOOT_LOG_H

#include <stdarg.h>
#include <stddef.h>
#include <stdint.h>

#include "mcuboot_port.h"

#define MCUBOOT_LOG_LEVEL_OFF      0
#define MCUBOOT_LOG_LEVEL_ERROR    1
#define MCUBOOT_LOG_LEVEL_WARNING  2
#define MCUBOOT_LOG_LEVEL_INFO     3
#define MCUBOOT_LOG_LEVEL_DEBUG    4

// Formats into a bounded buffer with mcuboot_vsnprintf() and writes the text with
// mcuboot_port_log_write(). Output longer than the internal buffer is truncated.
void mcuboot_log(int level, const char *fmt, ...);

// Subset of vsnprintf without float, heap or locale: %d %i %u %x %X %c %s %p %%, the
// length modifiers l and z, field width and zero padding, and '-' for left alignment.
// Always NUL terminates when size > 0. Returns the length the full output would have,
// as vsnprintf does.
int mcuboot_vsnprintf(char *buf, size_t size, const char *fmt, va_list ap);

// Failure handler behind ASSERT(). Logs the location, records LOG_ASSERT in the update log
// when the log area is usable and then leaves for recovery: the bootloader role calls
// mcuboot_fault_recover(), the app role resets so the bootloader decides. Never returns
// and never loops silently.
MCUBOOT_NORETURN void mcuboot_assert_fail(const char *file, int line);

// Bootloader role only, implemented by the main flow: enter DFU recovery with the
// fault cause. Does not return.
MCUBOOT_NORETURN void mcuboot_fault_recover(void);

#endif // MICROPY_INCLUDED_SHARED_MCUBOOT_MCUBOOT_LOG_H
