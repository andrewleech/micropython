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

#define _GNU_SOURCE
#include <errno.h>
#include <stdio.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>

#include "mcuboot_config/mcuboot_config.h"
#include "mboot_port.h"
#include "mboot_log.h"
#include "core.h"

// The port functions the glue calls, implemented for the host. The flash functions are in
// tests/mboot/host/fake_flash.c.

core_shared_t *core_shared;

void core_port_init(void) {
    if (core_shared == NULL) {
        core_shared = mmap(NULL, sizeof(*core_shared), PROT_READ | PROT_WRITE, MAP_SHARED | MAP_ANONYMOUS, -1, 0);
        if (core_shared == MAP_FAILED) {
            perror("mmap");
            _exit(2);
        }
        memset(core_shared, 0, sizeof(*core_shared));
    }
}

void mboot_port_wdt_feed(void) {
    core_shared->wdt_feeds++;
}

#if defined(MCUBOOT_FIH_PROFILE_HIGH)
// Entropy of the random delays of the high FIH profile: a xorshift generator, a fixed sequence
// per process so that a failing run repeats.
uint8_t mboot_port_entropy_u8(void) {
    static uint32_t x = 2463534242u;
    x ^= x << 13;
    x ^= x >> 17;
    x ^= x << 5;
    return (uint8_t)x;
}
#endif

void mboot_port_log_write(const char *s, size_t n) {
    core_shared->log_lines++;
    if (memmem(s, n, "unreadable", 10) != NULL) {
        core_shared->log_warnings++;
    }
    if (n >= sizeof(core_shared->log_tail)) {
        s += n - (sizeof(core_shared->log_tail) - 1);
        n = sizeof(core_shared->log_tail) - 1;
    }
    if (core_shared->log_tail_len + n >= sizeof(core_shared->log_tail)) {
        size_t keep = sizeof(core_shared->log_tail) / 2;
        memmove(core_shared->log_tail, core_shared->log_tail + core_shared->log_tail_len - keep, keep);
        core_shared->log_tail_len = keep;
    }
    memcpy(core_shared->log_tail + core_shared->log_tail_len, s, n);
    core_shared->log_tail_len += n;
    if (core_shared->log_echo) {
        fwrite(s, 1, n, stdout);
        fflush(stdout);
    }
}

uint32_t mboot_port_retention_read(void) {
    return core_shared->retention;
}

void mboot_port_retention_write(uint32_t v) {
    core_shared->retention = v;
}

void *mboot_port_request_ram(size_t *size_out) {
    if (size_out != NULL) {
        *size_out = sizeof(core_shared->req_ram);
    }
    return core_shared->req_ram;
}

MBOOT_NORETURN void mboot_port_reset(void) {
    _exit(CORE_EXIT_RESET);
}

// Recovery entry of the assert handler. The main flow enters DFU here; the harness only records
// that recovery was reached and where.
MBOOT_NORETURN void mboot_fault_recover(void) {
    core_shared->out.kind = CORE_OUT_RECOVERY;
    if (core_shared->out.where[0] == '\0') {
        strcpy(core_shared->out.where, "mboot_fault_recover");
    }
    _exit(CORE_EXIT_RECOVERY);
}

// The update stream reader belongs to the filesystem front end, which this harness does not
// include. The flash map calls it for the stream device when the board enables FAT or littlefs.
int mboot_stream_area_read(uint32_t off, void *dst, uint32_t len) {
    (void)off;
    (void)dst;
    (void)len;
    return -ENOTSUP;
}

// Fault injection hook (MBOOT_TEST_FI). The 16 bytes at +0x3F0 of the request RAM are
// { magic, target, counter, mode } (mode bits 0-1: 0 before, 1 after; bit 7: trace). The hook
// resets when the counter reaches the target, before the operation (mode 0) or after it (mode 1).
void mboot_port_fi_hook(int op, uint8_t dev, uint32_t off, uint32_t len, int phase) {
    (void)op;
    (void)dev;
    (void)off;
    (void)len;
    volatile uint32_t *st = (volatile uint32_t *)(core_shared->req_ram + 0x3F0);
    if (st[1] != 0 && st[2] == st[1] && (st[3] & 3u) == (uint32_t)phase) {
        mboot_port_reset();
    }
}

// tinycrypt's ecc.c names default_CSPRNG in a data initialiser. Signature verification never
// calls it and the linker drops the reference with --gc-sections, but the sanitizer builds keep
// it. This stub makes the link work and returns 0 (failure) if it is ever called.
int default_CSPRNG(uint8_t *dest, unsigned int size) {
    (void)dest;
    (void)size;
    return 0;
}
