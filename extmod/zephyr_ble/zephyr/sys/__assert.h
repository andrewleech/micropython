/*
 * Zephyr sys/__assert.h wrapper for MicroPython
 * Maps Zephyr assertions to standard assert
 */

#ifndef ZEPHYR_SYS_ASSERT_H_
#define ZEPHYR_SYS_ASSERT_H_

#include <assert.h>

// Zephyr assertion macros map to standard assert
#define __ASSERT(test, fmt, ...) assert(test)
#define __ASSERT_NO_MSG(test) assert(test)

// Evaluation macro (executes expression even if asserts disabled)
#define __ASSERT_EVAL(expr1, expr2, test, fmt, ...) \
    do { (void)(expr1); (void)(expr2); assert(test); } while (0)

#endif /* ZEPHYR_SYS_ASSERT_H_ */
