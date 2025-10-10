/*
 * Zephyr debug/stack.h wrapper for MicroPython
 * Stack debugging stubs (not used in MicroPython)
 */

#ifndef ZEPHYR_DEBUG_STACK_H_
#define ZEPHYR_DEBUG_STACK_H_

// Stack analysis functions (no-op)
static inline void stack_analyze(const char *name, const char *stack, size_t size) {
    (void)name;
    (void)stack;
    (void)size;
}

#endif /* ZEPHYR_DEBUG_STACK_H_ */
