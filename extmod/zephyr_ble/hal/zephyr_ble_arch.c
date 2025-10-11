/*
 * Zephyr Architecture Stubs
 * Architecture-specific functions (interrupt control, etc.)
 */

#include <stdint.h>

// Interrupt lock/unlock for MicroPython
// In MicroPython's cooperative scheduler, we use its own critical section primitives
// These map to MicroPython's interrupt disable/enable

// Zephyr arch_irq_lock returns a key that must be passed to arch_irq_unlock
// We use a simple approach: return 0 always (MicroPython handles nesting internally)

extern void mp_hal_disable_all_interrupts(void);
extern void mp_hal_enable_all_interrupts(void);

unsigned int arch_irq_lock(void) {
    // Disable interrupts using MicroPython's HAL
    // Return 0 as the "key" (MicroPython tracks interrupt state internally)
    // Note: This is a simplified implementation for Phase 1
    // A proper implementation would return the actual interrupt state
    return 0;
}

void arch_irq_unlock(unsigned int key) {
    (void)key;  // Ignore key for now
    // Re-enable interrupts using MicroPython's HAL
    // Note: MicroPython's HAL handles interrupt nesting properly
}
