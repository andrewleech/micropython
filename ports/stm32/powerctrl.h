/*
 * This file is part of the MicroPython project, http://micropython.org/
 *
 * The MIT License (MIT)
 *
 * Copyright (c) 2018 Damien P. George
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
 * OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
 * THE SOFTWARE.
 */
#ifndef MICROPY_INCLUDED_STM32_POWERCTRL_H
#define MICROPY_INCLUDED_STM32_POWERCTRL_H

#include <stdbool.h>
#include <stdint.h>

// The reset status register and its flags: RCC->RSR on H5/H7/N6, RCC->CSR elsewhere.
#if defined(STM32G0)
// G0 has BOR and POR combined
#define RCC_CSR_BORRSTF RCC_CSR_PWRRSTF
#define RCC_CSR_PORRSTF RCC_CSR_PWRRSTF
#endif

#if defined(STM32H5) || defined(STM32N6)
#define RCC_SR          RSR
#define RCC_SR_IWDGRSTF RCC_RSR_IWDGRSTF
#define RCC_SR_WWDGRSTF RCC_RSR_WWDGRSTF
#define RCC_SR_BORRSTF  RCC_RSR_BORRSTF
#define RCC_SR_PINRSTF  RCC_RSR_PINRSTF
#define RCC_SR_SFTRSTF  RCC_RSR_SFTRSTF
#define RCC_SR_RMVF     RCC_RSR_RMVF
#if defined(RCC_RSR_LPWRRSTF)
#define RCC_SR_LPWRRSTF RCC_RSR_LPWRRSTF
#endif
#elif defined(STM32H7)
#define RCC_SR          RSR
#define RCC_SR_IWDGRSTF RCC_RSR_IWDG1RSTF
#define RCC_SR_WWDGRSTF RCC_RSR_WWDG1RSTF
#define RCC_SR_PORRSTF  RCC_RSR_PORRSTF
#define RCC_SR_BORRSTF  RCC_RSR_BORRSTF
#define RCC_SR_PINRSTF  RCC_RSR_PINRSTF
#if defined(STM32H747xx)
#define RCC_SR_SFTRSTF  RCC_RSR_SFT2RSTF
#else
#define RCC_SR_SFTRSTF  RCC_RSR_SFTRSTF
#endif
#define RCC_SR_RMVF     RCC_RSR_RMVF
#else
#define RCC_SR          CSR
#define RCC_SR_IWDGRSTF RCC_CSR_IWDGRSTF
#define RCC_SR_WWDGRSTF RCC_CSR_WWDGRSTF
#if defined(RCC_CSR_PORRSTF)
#define RCC_SR_PORRSTF  RCC_CSR_PORRSTF
#endif
#if defined(RCC_CSR_BORRSTF)
#define RCC_SR_BORRSTF  RCC_CSR_BORRSTF
#endif
#define RCC_SR_PINRSTF  RCC_CSR_PINRSTF
#define RCC_SR_SFTRSTF  RCC_CSR_SFTRSTF
#define RCC_SR_RMVF     RCC_CSR_RMVF
#if defined(RCC_CSR_LPWRRSTF)
#define RCC_SR_LPWRRSTF RCC_CSR_LPWRRSTF
#endif
#endif

// Whether this MCU has an independent PLL which can generate 48MHz for USB.
#if defined(STM32F413xx)
// STM32F413 uses PLLI2S as secondary PLL.
#define HAVE_PLL48 1
#define RCC_CR_PLL48_ON RCC_CR_PLLI2SON
#define RCC_CR_PLL48_RDY RCC_CR_PLLI2SRDY
#elif defined(STM32F7)
// STM32F7 uses PLLSAI as secondary PLL.
#define HAVE_PLL48 1
#define RCC_CR_PLL48_ON RCC_CR_PLLSAION
#define RCC_CR_PLL48_RDY RCC_CR_PLLSAIRDY
#else
// MCU does not have a secondary PLL.
#define HAVE_PLL48 0
#endif

static inline void powerctrl_disable_hsi_if_unused(void) {
    #if !MICROPY_HW_CLK_USE_HSI && (defined(STM32F4) || defined(STM32F7) || defined(STM32H7))
    // Disable HSI if it's not used to save a little bit of power
    __HAL_RCC_HSI_DISABLE();
    #endif
}

#if defined(STM32WB)
void stm32_system_init(void);
#else
static inline void stm32_system_init(void) {
    SystemInit();

    #if defined(STM32N6)
    // The ROM bootloader uses PLL1 to set the CPU to 400MHz, so update
    // the value of SystemCoreClock to reflect the hardware state.
    SystemCoreClockUpdate();
    #endif
}
#endif

void SystemClock_Config(void);

MP_NORETURN void powerctrl_mcu_reset(void);
MP_NORETURN void powerctrl_enter_bootloader(uint32_t r0, uint32_t bl_addr);
void powerctrl_check_enter_bootloader(void);

void powerctrl_config_systick(void);
int powerctrl_rcc_clock_config_pll(RCC_ClkInitTypeDef *rcc_init, uint32_t sysclk_mhz, bool need_pllsai);
int powerctrl_set_sysclk(uint32_t sysclk, uint32_t ahb, uint32_t apb1, uint32_t apb2);
void powerctrl_enter_stop_mode(void);
MP_NORETURN void powerctrl_enter_standby_mode(void);

#endif // MICROPY_INCLUDED_STM32_POWERCTRL_H
