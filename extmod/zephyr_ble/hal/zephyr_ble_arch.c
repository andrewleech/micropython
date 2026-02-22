/*
 * Zephyr Architecture Stubs
 * Architecture-specific functions (interrupt control, etc.)
 */

#include <stdint.h>

// Platform-specific interrupt control
#if defined(__arm__) || defined(__thumb__)
// ARM Cortex-M: use PRIMASK for interrupt control.

unsigned int arch_irq_lock(void) {
    unsigned int key;
    __asm volatile ("mrs %0, primask" : "=r" (key));
    __asm volatile ("cpsid i" ::: "memory");
    return key;
}

void arch_irq_unlock(unsigned int key) {
    __asm volatile ("msr primask, %0" :: "r" (key) : "memory");
}

#else
// Unix and other non-ARM ports — no real interrupt control needed

unsigned int arch_irq_lock(void) {
    return 0;
}

void arch_irq_unlock(unsigned int key) {
    (void)key;
}

#endif
