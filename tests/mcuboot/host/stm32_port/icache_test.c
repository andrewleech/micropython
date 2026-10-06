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

// Host test of mcuboot_stm32_icache_invalidate() (ports/stm32/mcuboot/port_stm32.h) against a
// register model: it waits for the end of the invalidation only for a bounded time and reports
// -ETIMEDOUT when the cache does not complete it.

#include <errno.h>
#include <stdio.h>

#include "port_stm32.h"

ICACHE_TypeDef fake_icache;

static int failed;

static void expect(const char *what, int got, int want) {
    if (got != want) {
        printf("FAIL %s: got %d, want %d\n", what, got, want);
        failed++;
    } else {
        printf("ok %s\n", what);
    }
}

int main(void) {
    // A disabled cache has nothing to invalidate: no request is made.
    fake_icache.CR = 0;
    fake_icache.SR = 0;
    fake_icache.FCR = 0;
    expect("disabled returns 0", mcuboot_stm32_icache_invalidate(), 0);
    expect("disabled makes no request", (int)(fake_icache.CR & ICACHE_CR_CACHEINV), 0);

    // The invalidation completes (the end flag is set): success, the flag is cleared again.
    fake_icache.CR = ICACHE_CR_EN;
    fake_icache.SR = ICACHE_SR_BSYENDF;
    fake_icache.FCR = 0;
    expect("completed returns 0", mcuboot_stm32_icache_invalidate(), 0);
    expect("completed requested the invalidation", (int)((fake_icache.CR & ICACHE_CR_CACHEINV) != 0), 1);
    expect("completed cleared the end flag", (int)((fake_icache.FCR & ICACHE_FCR_CBSYENDF) != 0), 1);

    // The end flag never comes: the wait is bounded and the failure is reported.
    fake_icache.CR = ICACHE_CR_EN;
    fake_icache.SR = 0;
    fake_icache.FCR = 0;
    expect("stuck returns -ETIMEDOUT", mcuboot_stm32_icache_invalidate(), -ETIMEDOUT);

    printf(failed ? "icache_test FAILED\n" : "icache_test PASSED\n");
    return failed != 0;
}
