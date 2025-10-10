/*
 * Zephyr toolchain.h wrapper for MicroPython
 * Compiler-specific attributes and macros
 */

#ifndef ZEPHYR_TOOLCHAIN_H_
#define ZEPHYR_TOOLCHAIN_H_

// Compiler attributes (most already defined in kernel.h, but repeat here)
#ifndef __packed
#define __packed __attribute__((__packed__))
#endif

#ifndef __aligned
#define __aligned(x) __attribute__((__aligned__(x)))
#endif

#ifndef __used
#define __used __attribute__((__used__))
#endif

#ifndef __unused
#define __unused __attribute__((__unused__))
#endif

#ifndef __maybe_unused
#define __maybe_unused __attribute__((__unused__))
#endif

#ifndef __deprecated
#define __deprecated __attribute__((__deprecated__))
#endif

#ifndef __weak
#define __weak __attribute__((__weak__))
#endif

#ifndef __noreturn
#define __noreturn __attribute__((__noreturn__))
#endif

#ifndef __always_inline
#define __always_inline inline __attribute__((__always_inline__))
#endif

#ifndef __noinline
#define __noinline __attribute__((__noinline__))
#endif

// Section attributes
#define __in_section(seg) __attribute__((__section__(seg)))

// Compiler barriers
#define compiler_barrier() __asm__ __volatile__("" ::: "memory")

// Optimization hints
#define likely(x) __builtin_expect(!!(x), 1)
#define unlikely(x) __builtin_expect(!!(x), 0)

// Unreachable code
#define CODE_UNREACHABLE __builtin_unreachable()

#endif /* ZEPHYR_TOOLCHAIN_H_ */
