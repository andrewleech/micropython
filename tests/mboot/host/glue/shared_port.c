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

#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#include "flash_map_backend/flash_map_backend.h"
#include "mboot_log.h"
#include "mboot_port.h"

#include "host_glue.h"

// Port functions the shared flash map backend and log code need besides the flash
// device (fake_flash.c). A failed assertion is logged by mboot_assert_fail() and then
// calls mboot_fault_recover(), which here ends the simulated boot like a bare-metal
// halt so the sweep records it; the file and line are taken from the log line.

static FILE *log_file;
static host_halt_hook_t halt_hook;

void host_log_set_file(FILE *f) {
    log_file = f;
}

void host_halt_set_hook(host_halt_hook_t fn) {
    halt_hook = fn;
}

void mboot_port_wdt_feed(void) {
}

void mboot_port_log_write(const char *s, size_t n) {
    if (log_file != NULL) {
        fwrite(s, 1, n, log_file);
        fflush(log_file);
    }
    if (n > 7 && memcmp(s, "ASSERT ", 7) == 0 && halt_hook != NULL) {
        char buf[96];
        size_t len = n - 7 < sizeof(buf) - 1 ? n - 7 : sizeof(buf) - 1;
        memcpy(buf, s + 7, len);
        buf[len] = '\0';
        char *colon = strrchr(buf, ':');
        if (colon != NULL) {
            *colon = '\0';
            halt_hook(buf, atoi(colon + 1));
        }
    }
}

void mboot_fault_recover(void) {
    _exit(66);
}


// There is no fsload stream device in the sweep.
int mboot_stream_area_read(uint32_t off, void *dst, uint32_t len) {
    (void)off;
    (void)dst;
    (void)len;
    return -ENOTSUP;
}
