/*
 * Zephyr arch/arch_interface.h wrapper for MicroPython
 * Provides architecture interrupt control functions
 */

#ifndef ZEPHYR_ARCH_ARCH_INTERFACE_H_
#define ZEPHYR_ARCH_ARCH_INTERFACE_H_

// Platform-specific interrupt control
#if defined(__arm__) && defined(PICO_BUILD)
// RP2 (Raspberry Pi Pico) - use Pico SDK functions
#include "hardware/sync.h"

static inline unsigned int arch_irq_lock(void) {
    return save_and_disable_interrupts();
}

static inline void arch_irq_unlock(unsigned int key) {
    restore_interrupts(key);
}

#else
// Unix and other ports - simplified stub implementation

static inline unsigned int arch_irq_lock(void) {
    return 0;
}

static inline void arch_irq_unlock(unsigned int key) {
    (void)key;
}

#endif

#endif /* ZEPHYR_ARCH_ARCH_INTERFACE_H_ */

