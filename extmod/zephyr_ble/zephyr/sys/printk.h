/*
 * Zephyr sys/printk.h wrapper for MicroPython
 * printk function (CONFIG_PRINTK=0, so stub)
 */

#ifndef ZEPHYR_SYS_PRINTK_H_
#define ZEPHYR_SYS_PRINTK_H_

#include <stdio.h>
#include <stdarg.h>

// Printf format attribute for type checking
#define __printf_like(f, a) __attribute__((__format__(__printf__, f, a)))

// printk is disabled (CONFIG_PRINTK=0)
// Map to no-op or printf depending on configuration
#ifdef MICROPY_DEBUG_ZEPHYR_BLE
#define printk printf
#else
static inline __printf_like(1, 2) void printk(const char *fmt, ...) {
    (void)fmt;
}
#endif

static inline __printf_like(1, 0) void vprintk(const char *fmt, va_list ap) {
    (void)fmt;
    (void)ap;
}

// snprintk/vsnprintk map to standard functions
#define snprintk snprintf
#define vsnprintk vsnprintf

#endif /* ZEPHYR_SYS_PRINTK_H_ */
