/*
 * CMSIS Core Wrapper for MicroPython Zephyr Integration
 *
 * This header bridges Zephyr's CMSIS expectations with MicroPython's CMSIS headers.
 * Zephyr expects <cmsis_core.h> but we need to include the proper CMSIS header
 * based on the CPU configuration.
 */

#ifndef MICROPYTHON_CMSIS_CORE_WRAPPER_H
#define MICROPYTHON_CMSIS_CORE_WRAPPER_H

// Include the appropriate CMSIS Core header based on CPU type
#if defined(CONFIG_CPU_CORTEX_M0)
#include "core_cm0.h"
#elif defined(CONFIG_CPU_CORTEX_M0PLUS)
#include "core_cm0plus.h"
#elif defined(CONFIG_CPU_CORTEX_M1)
#include "core_cm1.h"
#elif defined(CONFIG_CPU_CORTEX_M3)
#include "core_cm3.h"
#elif defined(CONFIG_CPU_CORTEX_M4)
#include "core_cm4.h"
#elif defined(CONFIG_CPU_CORTEX_M7)
#include "core_cm7.h"
#elif defined(CONFIG_CPU_CORTEX_M23)
#include "core_cm23.h"
#elif defined(CONFIG_CPU_CORTEX_M33)
#include "core_cm33.h"
#elif defined(CONFIG_CPU_CORTEX_M35P)
#include "core_cm35p.h"
#else
#error "Unsupported Cortex-M CPU type for CMSIS"
#endif

#endif // MICROPYTHON_CMSIS_CORE_WRAPPER_H
