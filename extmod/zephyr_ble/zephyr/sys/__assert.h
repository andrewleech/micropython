/*
 * Zephyr assert wrapper for MicroPython
 */

#ifndef ZEPHYR_SYS___ASSERT_H_
#define ZEPHYR_SYS___ASSERT_H_

#include <stdio.h>
#include <assert.h>

// Zephyr assertions
#define __ASSERT(test, fmt, ...) \
    do { \
        if (!(test)) { \
            printf("ASSERTION FAIL [%s:%d]: " fmt "\n", __FILE__, __LINE__,##__VA_ARGS__); \
            assert(test); \
        } \
    } while (0)

#define __ASSERT_NO_MSG(test) \
    do { \
        if (!(test)) { \
            printf("ASSERTION FAIL [%s:%d]\n", __FILE__, __LINE__); \
            assert(test); \
        } \
    } while (0)

#define __ASSERT_EVAL(expr1, expr2, test, fmt, ...) \
    do { \
        expr1; \
        expr2; \
        __ASSERT(test, fmt,##__VA_ARGS__); \
    } while (0)

#endif /* ZEPHYR_SYS___ASSERT_H_ */
