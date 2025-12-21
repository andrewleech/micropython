/*
 * This file is part of the MicroPython project, http://micropython.org/
 *
 * The MIT License (MIT)
 *
 * Copyright (c) 2025 MicroPython Contributors
 *
 * Permission is hereby granted, free of charge, to any person obtaining a copy
 * of this software and associated documentation files (the "Software"), to deal
 * in the Software without restriction, including without limitation the rights
 * to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
 * copies of the Software, and to permit persons to whom the Software is
 * furnished to do so, subject to the following conditions:
 *
 * The above copyright notice and this permission notice shall be included in
 * all copies or substantial portions of the Software.
 *
 * THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
 * IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
 * FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
 * AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
 * LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
 * OUT OF THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
 */

#ifndef MICROPY_INCLUDED_EXTMOD_ZEPHYR_BLE_CMSIS_CORE_H
#define MICROPY_INCLUDED_EXTMOD_ZEPHYR_BLE_CMSIS_CORE_H

// Zephyr's asm_inline_gcc.h includes <cmsis_core.h> expecting CMSIS intrinsics.
// This stub header provides port-specific CMSIS integration.

// Port-specific CMSIS headers
#if defined(__ZEPHYR_BLE_RP2_PORT__)
// RP2: Use pico-sdk CMSIS stub
// The cmsis_core_headers target is linked via CMake, which adds the include path
// Include device header first (defines IRQn_Type), then core header
#if __has_include("RP2040.h")
#include "RP2040.h"
#elif __has_include("RP2350.h")
#include "RP2350.h"
#else
#error "RP2 device headers not found. Ensure cmsis_core_headers is linked."
#endif

#elif defined(__ZEPHYR_BLE_STM32_PORT__)
// STM32: Use STM32 HAL CMSIS headers (already included via board support)
// No action needed - STM32 ports include appropriate CMSIS headers automatically

#else
// Fallback: Provide minimal stub implementations of required CMSIS intrinsics
// This ensures compilation succeeds even if proper CMSIS headers aren't available

#warning "No port-specific CMSIS headers found. Using minimal stubs."

static inline void __enable_irq(void) {
    __asm__ volatile ("cpsie i" : : : "memory");
}

static inline void __disable_irq(void) {
    __asm__ volatile ("cpsid i" : : : "memory");
}

static inline void __ISB(void) {
    __asm__ volatile ("isb 0xF" : : : "memory");
}

static inline void __DSB(void) {
    __asm__ volatile ("dsb 0xF" : : : "memory");
}

static inline uint32_t __get_PRIMASK(void) {
    uint32_t result;
    __asm__ volatile ("mrs %0, primask" : "=r" (result));
    return result;
}

static inline void __set_PRIMASK(uint32_t priMask) {
    __asm__ volatile ("msr primask, %0" : : "r" (priMask) : "memory");
}

static inline uint32_t __get_BASEPRI(void) {
    uint32_t result;
    __asm__ volatile ("mrs %0, basepri" : "=r" (result));
    return result;
}

static inline void __set_BASEPRI(uint32_t basePri) {
    __asm__ volatile ("msr basepri, %0" : : "r" (basePri) : "memory");
}

static inline void __set_BASEPRI_MAX(uint32_t basePri) {
    __asm__ volatile ("msr basepri_max, %0" : : "r" (basePri) : "memory");
}

#endif

#endif // MICROPY_INCLUDED_EXTMOD_ZEPHYR_BLE_CMSIS_CORE_H
