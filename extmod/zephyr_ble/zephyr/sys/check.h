/*
 * Zephyr check macros for MicroPython
 */

#ifndef ZEPHYR_SYS_CHECK_H_
#define ZEPHYR_SYS_CHECK_H_

#include "sys/__assert.h"

// Runtime checks with return on failure
#define CHECKIF(expr) if (expr)

#endif /* ZEPHYR_SYS_CHECK_H_ */
