/*
 * This file is part of the MicroPython project, http://micropython.org/
 *
 * SPDX-License-Identifier: Apache-2.0
 *
 * Minimal thread_stack.h stub for Zephyr BLE without threading
 *
 * In full Zephyr, this file defines thread stack macros for kernel threads.
 * For MicroPython, we don't use threading, so these are no-ops.
 */

#ifndef ZEPHYR_INCLUDE_KERNEL_THREAD_STACK_H_
#define ZEPHYR_INCLUDE_KERNEL_THREAD_STACK_H_

// Define thread stack element type for MicroPython
// In full Zephyr, this is architecture-specific (struct z_thread_stack_element)
// For MicroPython, we use a simple byte wrapper
struct z_thread_stack_element {
    uint8_t byte;
};

// Typedef used by Zephyr's stack macros
typedef struct z_thread_stack_element k_thread_stack_t;

// Thread stack definition macro - no-op in MicroPython (no threads)
// In full Zephyr, this allocates a stack buffer with alignment and guards
// Note: Don't add 'static' here - the calling code may add it
#define K_KERNEL_STACK_DEFINE(sym, size) \
    k_thread_stack_t __attribute__((unused)) sym[size]

// Thread stack array definition - no-op in MicroPython
#define K_KERNEL_STACK_ARRAY_DEFINE(sym, nmemb, size) \
    k_thread_stack_t __attribute__((unused)) sym[nmemb][size]

// Thread stack member definition - for use in structures
#define K_KERNEL_STACK_MEMBER(sym, size) \
    k_thread_stack_t sym[size]

// Thread stack size calculation - return size as-is (no alignment needed)
#define K_KERNEL_STACK_SIZEOF(sym) sizeof(sym)

// Stack buffer retrieval - return pointer to buffer
#define K_KERNEL_STACK_BUFFER(sym) ((void *)(sym))

// Thread stack size macro (for non-kernel threads) - just return sizeof
#define K_THREAD_STACK_SIZEOF(sym) sizeof(sym)

#endif /* ZEPHYR_INCLUDE_KERNEL_THREAD_STACK_H_ */
