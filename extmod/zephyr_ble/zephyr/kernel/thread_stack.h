/*
 * Zephyr kernel/thread_stack.h wrapper for MicroPython
 * Thread stack macros - no-op (no threading)
 */

#ifndef ZEPHYR_KERNEL_THREAD_STACK_H_
#define ZEPHYR_KERNEL_THREAD_STACK_H_

// Stack definition macros (no-op in MicroPython - caller adds 'static')
#ifndef K_KERNEL_STACK_DEFINE
#define K_KERNEL_STACK_DEFINE(sym, size) \
    char sym[1] __attribute__((unused))
#endif

#define K_KERNEL_STACK_ARRAY_DEFINE(sym, nmemb, size) \
    char sym[nmemb][1] __attribute__((unused))

#define K_KERNEL_STACK_MEMBER(sym, size) char sym[1]

// Stack size calculation (always returns 1)
#define K_KERNEL_STACK_SIZEOF(sym) 1
#define K_KERNEL_STACK_LEN(size) 1

// Thread stack declaration
#define K_THREAD_STACK_DEFINE(sym, size) K_KERNEL_STACK_DEFINE(sym, size)
#define K_THREAD_STACK_ARRAY_DEFINE(sym, nmemb, size) \
    K_KERNEL_STACK_ARRAY_DEFINE(sym, nmemb, size)
#define K_THREAD_STACK_MEMBER(sym, size) K_KERNEL_STACK_MEMBER(sym, size)
#define K_THREAD_STACK_SIZEOF(sym) 1

#endif /* ZEPHYR_KERNEL_THREAD_STACK_H_ */
