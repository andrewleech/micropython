/*
 * This file is part of the MicroPython project, http://micropython.org/
 *
 * The MIT License (MIT)
 *
 * Copyright (c) 2025 MicroPython Developers
 *
 * STM32-specific Zephyr threading integration
 */

#include "py/runtime.h"
#include "systick.h"

#if MICROPY_ZEPHYR_THREADING

// Override the weak symbol from cortex_m_arch.c to inject STM32 systick infrastructure
// This ensures uwTick, soft timers, and systick dispatch callbacks work with Zephyr threading
void mp_zephyr_port_systick_hook(void) {
    systick_process();
}

#endif // MICROPY_ZEPHYR_THREADING
