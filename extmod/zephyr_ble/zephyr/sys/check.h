/*
 * Zephyr sys/check.h wrapper for MicroPython
 * Runtime check macros with error returns
 */

#ifndef ZEPHYR_SYS_CHECK_H_
#define ZEPHYR_SYS_CHECK_H_

#include <errno.h>

// Check macros that return error codes on failure
// These are used for parameter validation in Zephyr APIs

#define CHECKIF(expr) if (expr)

// Return error code if condition is true
#define CHECKIF_RETURN(expr, retval) \
    do { if (expr) { return retval; } } while (0)

// Common error return macros
#define __ASSERT_NO_MSG(test) \
    do { if (!(test)) { return -EINVAL; } } while (0)

#endif /* ZEPHYR_SYS_CHECK_H_ */
