/*
 * This file is part of the MicroPython project, http://micropython.org/
 *
 * SPDX-License-Identifier: Apache-2.0
 *
 * Minimal cmsis_core.h stub for Zephyr BLE without OS
 *
 * In full Zephyr, this file provides CMSIS core headers for the target.
 * For MicroPython, we redirect to the port-specific CMSIS headers.
 */

#ifndef ZEPHYR_CMSIS_CORE_H_
#define ZEPHYR_CMSIS_CORE_H_

// Include port-specific CMSIS core headers based on the target MCU

#if defined(STM32WB55xx)
// STM32WB series - core_cm4.h is included via stm32wb55xx.h
#include "stm32wb55xx.h"
#elif defined(STM32WB)
// Generic STM32WB
#include "stm32wbxx.h"
#elif defined(STM32F4)
#include "stm32f4xx.h"
#elif defined(STM32F7)
#include "stm32f7xx.h"
#elif defined(STM32H7)
#include "stm32h7xx.h"
#elif defined(RP2040) || defined(PICO_RP2040)
// RP2040 - CMSIS headers are in pico SDK
#include "hardware/platform_defs.h"
#elif defined(ESP32)
// ESP32 - include Xtensa CMSIS equivalent
#else
// Fallback: try to detect architecture and provide minimal definitions
#ifndef __CORTEX_M
#if defined(__ARM_ARCH_6M__)
#define __CORTEX_M 0
#elif defined(__ARM_ARCH_7M__)
#define __CORTEX_M 3
#elif defined(__ARM_ARCH_7EM__)
#define __CORTEX_M 4
#elif defined(__ARM_ARCH_8M_BASE__)
#define __CORTEX_M 23
#elif defined(__ARM_ARCH_8M_MAIN__)
#define __CORTEX_M 33
#elif defined(__ARM_ARCH_8_1M_MAIN__)
#define __CORTEX_M 55
#endif
#endif
#endif

#endif /* ZEPHYR_CMSIS_CORE_H_ */
