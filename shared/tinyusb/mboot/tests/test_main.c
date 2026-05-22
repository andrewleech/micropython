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

// test_main.c — test runner entry point.
//
// Collects per-module pass/fail counts and exits nonzero if any test failed.
// Each test module exposes a single function:
//   int test_<module>(void);
// which returns the number of test failures (0 = all passed).

#include <stdio.h>
#include <stdlib.h>

// ---------------------------------------------------------------------------
// Assertion helpers — used by all test_*.c files via test_main.h declarations
// ---------------------------------------------------------------------------

// test_fail — print a failure message and return -1.
// Not called directly; used by TEST_ASSERT macros in test_main.h.
void test_fail(const char *file, int line, const char *expr) {
    printf("  FAIL %s:%d: %s\n", file, line, expr);
}

// ---------------------------------------------------------------------------
// Per-module entry points (defined in each test_*.c)
// ---------------------------------------------------------------------------

int test_dfu(void);
int test_vendor(void);
int test_region(void);
int test_elem(void);
int test_usbd(void);

// ---------------------------------------------------------------------------
// main
// ---------------------------------------------------------------------------

int main(void) {
    int total_failures = 0;
    int result;

    printf("=== test_dfu ===\n");
    result = test_dfu();
    if (result == 0) {
        printf("  PASS (all tests)\n");
    } else {
        printf("  FAIL (%d failure(s))\n", result);
    }
    total_failures += result;

    printf("=== test_vendor ===\n");
    result = test_vendor();
    if (result == 0) {
        printf("  PASS (all tests)\n");
    } else {
        printf("  FAIL (%d failure(s))\n", result);
    }
    total_failures += result;

    printf("=== test_region ===\n");
    result = test_region();
    if (result == 0) {
        printf("  PASS (all tests)\n");
    } else {
        printf("  FAIL (%d failure(s))\n", result);
    }
    total_failures += result;

    printf("=== test_elem ===\n");
    result = test_elem();
    if (result == 0) {
        printf("  PASS (all tests)\n");
    } else {
        printf("  FAIL (%d failure(s))\n", result);
    }
    total_failures += result;

    printf("=== test_usbd ===\n");
    result = test_usbd();
    if (result == 0) {
        printf("  PASS (all tests)\n");
    } else {
        printf("  FAIL (%d failure(s))\n", result);
    }
    total_failures += result;

    printf("\n");
    if (total_failures == 0) {
        printf("ALL TESTS PASSED\n");
        return 0;
    } else {
        printf("TOTAL FAILURES: %d\n", total_failures);
        return 1;
    }
}
