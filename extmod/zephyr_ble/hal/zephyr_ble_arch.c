/*
 * Zephyr Architecture Stubs
 * Architecture-specific functions (interrupt control, etc.)
 */

#include <stdint.h>

// Platform-specific interrupt control
#if defined(__arm__) && defined(PICO_BUILD)
// RP2 (Raspberry Pi Pico) - use Pico SDK functions
#include "hardware/sync.h"

unsigned int arch_irq_lock(void) {
    return save_and_disable_interrupts();
}

void arch_irq_unlock(unsigned int key) {
    restore_interrupts(key);
}

#else
// Unix and other ports - simplified stub implementation
// In MicroPython's cooperative scheduler, we typically don't need real interrupt control

unsigned int arch_irq_lock(void) {
    // Return 0 as a placeholder key
    return 0;
}

void arch_irq_unlock(unsigned int key) {
    (void)key;  // Unused in stub implementation
}

#endif
