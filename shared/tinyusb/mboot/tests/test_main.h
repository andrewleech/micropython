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

#ifndef MBOOT_TESTS_TEST_MAIN_H
#define MBOOT_TESTS_TEST_MAIN_H

// test_main.h — assertion macros for mboot unit tests.
//
// Each test function has the signature:
//   static int test_<name>(int *failures);
// where *failures is incremented on each assertion failure.
//
// TEST_ASSERT(expr)        — fail if expr is false; return -1 from the test.
// TEST_ASSERT_EQ(a, b)     — fail if a != b; report both values; return -1.
// TEST_ASSERT_NULL(ptr)    — fail if ptr != NULL.
// TEST_ASSERT_NOTNULL(ptr) — fail if ptr == NULL.
// TEST_ASSERT_GE(a, b)     — fail if a < b.
//
// RUN_TEST(fn, failures)   — call fn(&failures); accumulate result in failures.

#include <stdio.h>

// Declared in test_main.c.
void test_fail(const char *file, int line, const char *expr);

#define TEST_ASSERT(expr) \
    do { \
        if (!(expr)) { \
            test_fail(__FILE__, __LINE__, #expr); \
            (*failures)++; \
            return -1; \
        } \
    } while (0)

#define TEST_ASSERT_EQ(a, b) \
    do { \
        long long _ta = (long long)(a); \
        long long _tb = (long long)(b); \
        if (_ta != _tb) { \
            printf("  FAIL %s:%d: %s == %s  (got %lld != %lld)\n", \
    __FILE__, __LINE__, #a, #b, _ta, _tb); \
            (*failures)++; \
            return -1; \
        } \
    } while (0)

#define TEST_ASSERT_NULL(ptr) \
    do { \
        if ((ptr) != NULL) { \
            test_fail(__FILE__, __LINE__, #ptr " == NULL"); \
            (*failures)++; \
            return -1; \
        } \
    } while (0)

#define TEST_ASSERT_NOTNULL(ptr) \
    do { \
        if ((ptr) == NULL) { \
            test_fail(__FILE__, __LINE__, #ptr " != NULL"); \
            (*failures)++; \
            return -1; \
        } \
    } while (0)

#define TEST_ASSERT_GE(a, b) \
    do { \
        long long _tga = (long long)(a); \
        long long _tgb = (long long)(b); \
        if (_tga < _tgb) { \
            printf("  FAIL %s:%d: %s >= %s  (got %lld < %lld)\n", \
    __FILE__, __LINE__, #a, #b, _tga, _tgb); \
            (*failures)++; \
            return -1; \
        } \
    } while (0)

// RUN_TEST — invoke a test function and accumulate failures.
// fn must have signature: static int fn(int *failures).
#define RUN_TEST(fn) \
    do { \
        int _f = failures; \
        fn(&failures); \
        if (failures == _f) { \
            printf("  ok: " #fn "\n"); \
        } \
    } while (0)

// RUN_TEST_XFAIL — run a test that is known to fail due to a documented defect
// in a production source file.  A failure is absorbed (not counted) and printed
// as "xfail"; a surprise pass is printed as "xpass" and counted as a failure.
// fn must have signature: static int fn(int *failures).
#define RUN_TEST_XFAIL(fn) \
    do { \
        int _xf_before = failures; \
        int _xf_dummy = _xf_before; \
        fn(&_xf_dummy); \
        if (_xf_dummy == _xf_before) { \
            printf("  XPASS (unexpected pass — remove xfail): " #fn "\n"); \
            failures++; \
        } else { \
            printf("  xfail (expected failure — Phase 2 defect): " #fn "\n"); \
        } \
    } while (0)

#endif // MBOOT_TESTS_TEST_MAIN_H
