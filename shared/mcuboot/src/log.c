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

#include <stdbool.h>

#include "mcuboot_config/mcuboot_config.h"
#include "mcuboot_log.h"
#include "mcuboot_types.h"
#include "mcuboot_updatelog.h"

// Longest formatted line; longer output is cut and still ends with a newline.
#define LOG_LINE_MAX (128)

void mcuboot_log(int level, const char *fmt, ...) {
    (void)level;
    char line[LOG_LINE_MAX];
    va_list ap;
    va_start(ap, fmt);
    int n = mcuboot_vsnprintf(line, sizeof(line), fmt, ap);
    va_end(ap);
    if (n >= (int)sizeof(line)) {
        n = sizeof(line) - 1;
        line[n - 1] = '\n';
    }
    if (n > 0) {
        mcuboot_port_log_write(line, (size_t)n);
    }
}

void mcuboot_assert_fail(const char *file, int line) {
    // A failure while handling a failure (the log or the update log asserting) goes straight
    // to recovery.
    static bool failing;
    if (!failing) {
        failing = true;
        #if MCUBOOT_LOG_LEVEL >= MCUBOOT_LOG_LEVEL_ERROR
        mcuboot_log(MCUBOOT_LOG_LEVEL_ERROR, "ASSERT %s:%d\n", file, line);
        #else
        (void)file;
        #endif
        #if defined(MCUBOOT_ROLE_BOOTLOADER)
        mcuboot_updatelog_append(LOG_ASSERT, MCUBOOT_RES_OK, SRC_BOOT, NULL, (uint32_t)line);
        #else
        mcuboot_updatelog_append(LOG_ASSERT, MCUBOOT_RES_OK, SRC_APP, NULL, (uint32_t)line);
        #endif
    }
    #if defined(MCUBOOT_ROLE_BOOTLOADER)
    mcuboot_fault_recover();
    #else
    mcuboot_port_reset();
    #endif
}
