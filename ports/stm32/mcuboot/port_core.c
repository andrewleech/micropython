/*
 * This file is part of the MicroPython project, http://micropython.org/
 *
 * The MIT License (MIT)
 *
 * Copyright (c) 2026 Andrew Leech
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

// Bootloader side of the mcuboot_port.h implementation for STM32H5 and STM32F7: clocks, caches,
// timing, reset cause, recovery inputs, LEDs, log UART, jump to the application and the fault
// handlers of the recovery policy (see "faults" below).
//
// The STM32H5 bootloader runs with TZEN=0 on the non-secure register aliases. The two series
// differ in the clock tree (PLL1 and the PWR voltage scaling on STM32H5, the main PLL on
// STM32F7), the caches (instruction cache on STM32H5, instruction and data cache of the
// Cortex-M7 on STM32F7), the GPIO and USART clock enables and the reset flags register; each of
// these is one #if block below. mcuboot_port_deinit() puts back every peripheral state changed
// here, so the application starts from the state a reset leaves.

#include <stddef.h>

// port_stm32.h includes the CMSIS header (which defines the series macro) and, through the
// MCUboot layout, the board file mpconfigboard.h; the board file is read once, here.
#include "port_stm32.h"
#if defined(STM32H5)
#include "stm32h5xx_hal.h"
#include "stm32h5xx_ll_rcc.h"
#include "stm32h5xx_ll_pwr.h"
#else
#include "stm32f7xx_hal.h"
#endif
#include "mphalport.h"

#include "mcuboot_config/mcuboot_config.h"
#include "mcuboot_config/mcuboot_logging.h"
#include "mcuboot_log.h"

// ---- configuration ----

// Run from PLL1 at the board clock (MICROPY_HW_CLK_* in mpconfigboard.h, the same values the
// application uses). 0 keeps the clock the reset leaves (HSI/2), for bring-up.
#ifndef MCUBOOT_STM32_PLL
#define MCUBOOT_STM32_PLL (1)
#endif

// Enable the instruction cache (STM32H5) or the instruction and data caches (STM32F7).
#ifndef MCUBOOT_STM32_ICACHE
#define MCUBOOT_STM32_ICACHE (1)
#endif

// The LEDs, USER button and log UART come from the board file (mpconfigboard.h), the same ones
// the application uses, with pins named pin_<port><number> (mboot/mphalport.h):
//   MICROPY_HW_LED1, _LED2, _LED3   alive, USB active and error (bits 0 to 2 of
//                                   mcuboot_port_led()); MICROPY_HW_LED_ON(pin) and
//                                   MICROPY_HW_LED_OFF(pin) give the polarity
//   MICROPY_HW_USRSW_PIN            forced entry while the input reads MICROPY_HW_USRSW_PRESSED
//                                   (0 or 1), with the pull MICROPY_HW_USRSW_PULL
//   MICROPY_HW_UART_REPL            the log UART (when MCUBOOT_LOG_LEVEL > 0). It must be
//                                   USART3 (PYB_UART_3), at MICROPY_HW_UART_REPL_BAUD on the pin
//                                   MICROPY_HW_UART3_TX; a board without a log UART sets
//                                   MCUBOOT_LOG_LEVEL 0
#define PYB_UART_3 (3)

#ifndef MICROPY_HW_LED1
#error "the board file does not define MICROPY_HW_LED1 (the bootloader shows alive on it)"
#endif
#ifndef MICROPY_HW_LED2
#error "the board file does not define MICROPY_HW_LED2 (the bootloader shows USB activity on it)"
#endif
#ifndef MICROPY_HW_LED3
#error "the board file does not define MICROPY_HW_LED3 (the bootloader shows errors on it)"
#endif
#ifndef MICROPY_HW_LED_ON
#error "the board file does not define MICROPY_HW_LED_ON(pin)"
#endif
#ifndef MICROPY_HW_LED_OFF
#error "the board file does not define MICROPY_HW_LED_OFF(pin)"
#endif
#ifndef MICROPY_HW_USRSW_PIN
#error "the board file does not define MICROPY_HW_USRSW_PIN (the bootloader's forced entry button)"
#endif
#ifndef MICROPY_HW_USRSW_PULL
#error "the board file does not define MICROPY_HW_USRSW_PULL"
#endif
#ifndef MICROPY_HW_USRSW_PRESSED
#error "the board file does not define MICROPY_HW_USRSW_PRESSED"
#endif
#if MCUBOOT_LOG_LEVEL > 0
#ifndef MICROPY_HW_UART_REPL
#error "the board file does not define MICROPY_HW_UART_REPL (the bootloader's log UART)"
#endif
#ifndef MICROPY_HW_UART_REPL_BAUD
#error "the board file does not define MICROPY_HW_UART_REPL_BAUD"
#endif
#if MICROPY_HW_UART_REPL != PYB_UART_3
#error "the bootloader's log UART driver is USART3: MICROPY_HW_UART_REPL has to be PYB_UART_3"
#endif
#ifndef MICROPY_HW_UART3_TX
#error "the board file does not define MICROPY_HW_UART3_TX"
#endif
#endif

// USART3_TX is alternate function 7 on all of its pins (PB10, PC10 and PD8).
#define MCUBOOT_STM32_UART_TX_AF (7)

// A board file pin is the GPIO port base | the pin number, as in ports/stm32/mboot.
#define MCUBOOT_PIN_GPIO(pin) ((GPIO_TypeDef *)((pin) & ~0xfu))
#define MCUBOOT_PIN_NUMBER(pin) ((pin) & 0xfu)

// The GPIO clock enable and reset registers, the USART transmit flag and the number of NVIC
// registers: GPIO ports are on AHB2 on STM32H5 and on AHB1 on STM32F7.
#if defined(STM32H5)
#define GPIO_CLK_ENR (RCC->AHB2ENR)
#define GPIO_RSTR (RCC->AHB2RSTR)
#define UART_TXE_FLAG (USART_ISR_TXE_TXFNF)
// 32 interrupts per NVIC enable register, as many registers as the device has interrupts.
#define NVIC_WORDS (5)
#else
#define GPIO_CLK_ENR (RCC->AHB1ENR)
#define GPIO_RSTR (RCC->AHB1RSTR)
#define UART_TXE_FLAG (USART_ISR_TXE)
#define NVIC_WORDS (4)
#endif

// Bounded busy-wait loop counts, in iterations at the reset clock.
#define CLOCK_WAIT_LOOPS (2000000)
#define UART_WAIT_LOOPS (200000)

// ---- state ----

extern const uint32_t g_pfnVectors[];

// Unique ID words, read before the caches are enabled (reading UID_BASE with the instruction
// cache enabled faults on STM32H5).
uint32_t mcuboot_stm32_uid[3];

#if defined(STM32F7)
// The HCLK frequency for the DWC2 driver of TinyUSB (it sets the USB turnaround time from it).
// The CMSIS system file that normally defines it is not part of the bootloader.
uint32_t SystemCoreClock;
#endif

static volatile uint32_t s_ticks;
static uint32_t s_hclk_hz;
#if defined(STM32H5)
// The USART3 kernel clock is PCLK1, which equals HCLK.
#define UART_CLK_HZ (s_hclk_hz)
#else
// PCLK1 is HCLK on the reset clock and HCLK / 4 with the PLL.
static uint32_t s_uart_hz;
#define UART_CLK_HZ (s_uart_hz)
#endif
static uint32_t s_rsr;
static bool s_pll_active;
static bool s_uart_ready;
static bool s_started;
// Reset bits of the GPIO ports the bootloader configured; mcuboot_port_deinit() resets them.
static uint32_t s_gpio_used;

// Register values at entry, which mcuboot_port_deinit() writes back.
#if defined(STM32H5)
static struct {
    uint32_t ahb2enr;
    uint32_t apb1lenr;
    uint32_t apb2enr;
    uint32_t apb3enr;
    uint32_t flash_acr;
    uint32_t voscr;
    uint32_t dbpcr;
} s_entry;
#else
static struct {
    uint32_t ahb1enr;
    uint32_t ahb3enr;
    uint32_t apb1enr;
    uint32_t apb2enr;
    uint32_t cfgr;
    uint32_t pllcfgr;
    uint32_t dckcfgr2;
    uint32_t flash_acr;
    uint32_t pwr_cr1;
} s_entry;
#endif

// ---- GPIO ----

#define GPIO_MODE_IN (0u)
#define GPIO_MODE_OUT (1u)
#define GPIO_MODE_AF (2u)

static void gpio_config(GPIO_TypeDef *port, uint32_t pin, uint32_t mode, uint32_t af, uint32_t pull) {
    uint32_t index = ((uint32_t)port - GPIOA_BASE) / (GPIOB_BASE - GPIOA_BASE);
    s_gpio_used |= 1u << index;
    GPIO_CLK_ENR |= 1u << index;
    (void)GPIO_CLK_ENR;
    uint32_t s2 = pin * 2;
    port->OTYPER &= ~(1u << pin);
    port->OSPEEDR |= 3u << s2;
    port->PUPDR = (port->PUPDR & ~(3u << s2)) | (pull << s2);
    uint32_t afr_shift = (pin & 7u) * 4;
    port->AFR[pin >> 3] = (port->AFR[pin >> 3] & ~(0xFu << afr_shift)) | (af << afr_shift);
    port->MODER = (port->MODER & ~(3u << s2)) | (mode << s2);
}

#if defined(MBOOT_SPIFLASH_ADDR) || defined(MBOOT_BOARD_EARLY_INIT)

// The pin services that the SPI flash drivers (drivers/bus/softqspi.c, drivers/memory/spiflash.c,
// ports/stm32/qspi.c) and the board hook of mboot expect from the HAL of the port, as in
// ports/stm32/mboot/main.c. The mode is MP_HAL_PIN_MODE_* of mboot/mphalport.h: bits 0 and 1 are
// the MODER field, bit 2 selects open drain. The pins are put back with their port in
// mcuboot_port_deinit().
void mp_hal_pin_config(uint32_t port_pin, uint32_t mode, uint32_t pull, uint32_t alt) {
    GPIO_TypeDef *gpio = MCUBOOT_PIN_GPIO(port_pin);
    uint32_t pin = MCUBOOT_PIN_NUMBER(port_pin);
    uint32_t index = ((uint32_t)gpio - GPIOA_BASE) / (GPIOB_BASE - GPIOA_BASE);
    s_gpio_used |= 1u << index;
    GPIO_CLK_ENR |= 1u << index;
    (void)GPIO_CLK_ENR;
    gpio->MODER = (gpio->MODER & ~(3u << (2 * pin))) | ((mode & 3u) << (2 * pin));
    gpio->OTYPER = (gpio->OTYPER & ~(1u << pin)) | ((mode >> 2) << pin);
    gpio->OSPEEDR = (gpio->OSPEEDR & ~(3u << (2 * pin))) | (3u << (2 * pin));
    gpio->PUPDR = (gpio->PUPDR & ~(3u << (2 * pin))) | (pull << (2 * pin));
    gpio->AFR[pin >> 3] = (gpio->AFR[pin >> 3] & ~(0xFu << (4 * (pin & 7u)))) | (alt << (4 * (pin & 7u)));
}

void mp_hal_pin_config_speed(uint32_t port_pin, uint32_t speed) {
    GPIO_TypeDef *gpio = MCUBOOT_PIN_GPIO(port_pin);
    uint32_t pin = MCUBOOT_PIN_NUMBER(port_pin);
    gpio->OSPEEDR = (gpio->OSPEEDR & ~(3u << (2 * pin))) | (speed << (2 * pin));
}

#endif

#if defined(MBOOT_BOARD_EARLY_INIT)

// The board hook of mboot (MBOOT_BOARD_EARLY_INIT in mpconfigboard.h, PYBD: the pull-up on the
// pin that selects 500 mA on WBUS-DIP28). mboot leaves what the hook sets in place when it starts
// the application and the application does not set it again, so mcuboot_port_deinit() runs the
// hook once more after it has reset the GPIO ports.
static void mboot_board_hook(void) {
    uint32_t initial_r0 = 0;
    MBOOT_BOARD_EARLY_INIT(&initial_r0);
    (void)initial_r0;
}

#else

static inline void mboot_board_hook(void) {
}

#endif

uint32_t mcuboot_stm32_hclk_hz(void) {
    return s_hclk_hz;
}

void mcuboot_stm32_gpio_af(GPIO_TypeDef *port, uint32_t pin, uint32_t af) {
    gpio_config(port, pin, GPIO_MODE_AF, af, 0);
}

#define LED_SET(pin, on) \
    do { \
        if (on) { \
            MICROPY_HW_LED_ON(pin); \
        } else { \
            MICROPY_HW_LED_OFF(pin); \
        } \
    } while (0)

void mcuboot_port_led(uint32_t mask) {
    LED_SET(MICROPY_HW_LED1, mask & 1u);
    LED_SET(MICROPY_HW_LED2, mask & 2u);
    LED_SET(MICROPY_HW_LED3, mask & 4u);
}

// ---- log UART ----

#if MCUBOOT_LOG_LEVEL > 0

static void uart_init(void) {
    gpio_config(MCUBOOT_PIN_GPIO(MICROPY_HW_UART3_TX), MCUBOOT_PIN_NUMBER(MICROPY_HW_UART3_TX), GPIO_MODE_AF, MCUBOOT_STM32_UART_TX_AF, 0);
    #if defined(STM32H5)
    RCC->APB1LENR |= RCC_APB1LENR_USART3EN;
    (void)RCC->APB1LENR;
    #else
    RCC->APB1ENR |= RCC_APB1ENR_USART3EN;
    (void)RCC->APB1ENR;
    #endif
    USART3->CR1 = 0;
    #if defined(STM32H5)
    USART3->PRESC = 0;
    #endif
    // The USART3 kernel clock is PCLK1 (reset selection).
    USART3->BRR = (UART_CLK_HZ + MICROPY_HW_UART_REPL_BAUD / 2) / MICROPY_HW_UART_REPL_BAUD;
    USART3->CR1 = USART_CR1_TE | USART_CR1_UE;
    s_uart_ready = true;
}

static void uart_putc(char c) {
    for (uint32_t n = UART_WAIT_LOOPS; n != 0; n--) {
        if (USART3->ISR & UART_TXE_FLAG) {
            USART3->TDR = (uint8_t)c;
            return;
        }
    }
}

#endif

void mcuboot_port_log_write(const char *s, size_t n) {
    #if MCUBOOT_LOG_LEVEL > 0
    if (!s_uart_ready) {
        return;
    }
    for (size_t i = 0; i < n; i++) {
        if (s[i] == '\n') {
            uart_putc('\r');
        }
        uart_putc(s[i]);
    }
    #else
    (void)s;
    (void)n;
    #endif
}

// ---- clocks ----

#define WAIT_UNTIL(cond) MCUBOOT_STM32_WAIT_UNTIL(cond, CLOCK_WAIT_LOOPS)

#if MCUBOOT_STM32_PLL && defined(MICROPY_HW_CLK_PLLM)

#if defined(MICROPY_HW_CLK_USE_HSI) && MICROPY_HW_CLK_USE_HSI
#define PLL_SOURCE_HZ (HSI_VALUE)
#else
#define PLL_SOURCE_HZ (HSE_VALUE)
#endif
#define PLL_HCLK_HZ (PLL_SOURCE_HZ / MICROPY_HW_CLK_PLLM * MICROPY_HW_CLK_PLLN / MICROPY_HW_CLK_PLLP)

#if defined(STM32H5)

// Flash programming delay for the clock, by the ranges documented with
// FLASH_PROGRAMMING_DELAY_x in stm32h5xx_hal_flash_ex.h.
#ifndef MCUBOOT_STM32_FLASH_PROG_DELAY
#if PLL_HCLK_HZ <= 70000000
#define MCUBOOT_STM32_FLASH_PROG_DELAY FLASH_PROGRAMMING_DELAY_0
#elif PLL_HCLK_HZ <= 185000000
#define MCUBOOT_STM32_FLASH_PROG_DELAY FLASH_PROGRAMMING_DELAY_1
#elif PLL_HCLK_HZ <= 225000000
#define MCUBOOT_STM32_FLASH_PROG_DELAY FLASH_PROGRAMMING_DELAY_2
#else
#define MCUBOOT_STM32_FLASH_PROG_DELAY FLASH_PROGRAMMING_DELAY_3
#endif
#endif

// Same sequence as SystemClock_Config() in ports/stm32/powerctrlboot.c for STM32H5. Returns
// false, with the oscillators off and the voltage scaling as at entry, when the voltage
// regulator, the oscillator or the PLL does not become ready; the bootloader then stays on
// the reset clock.
static bool clock_pll_start(void) {
    LL_PWR_SetRegulVoltageScaling(LL_PWR_REGU_VOLTAGE_SCALE0);
    if (!WAIT_UNTIL(PWR->VOSSR & PWR_VOSSR_VOSRDY)) {
        goto fail;
    }

    #if defined(MICROPY_HW_CLK_USE_HSI) && MICROPY_HW_CLK_USE_HSI
    LL_RCC_HSI_Enable();
    if (!WAIT_UNTIL(LL_RCC_HSI_IsReady())) {
        goto fail;
    }
    const uint32_t pll1_source = LL_RCC_PLL1SOURCE_HSI;
    #else
    #if defined(MICROPY_HW_CLK_USE_BYPASS) && MICROPY_HW_CLK_USE_BYPASS
    LL_RCC_HSE_EnableBypass();
    #endif
    LL_RCC_HSE_Enable();
    if (!WAIT_UNTIL(LL_RCC_HSE_IsReady())) {
        goto fail;
    }
    const uint32_t pll1_source = LL_RCC_PLL1SOURCE_HSE;
    #endif

    LL_RCC_PLL1_ConfigDomain_SYS(pll1_source, MICROPY_HW_CLK_PLLM, MICROPY_HW_CLK_PLLN, MICROPY_HW_CLK_PLLP);
    LL_RCC_PLL1_SetFRACN(MICROPY_HW_CLK_PLLFRAC);
    LL_RCC_PLL1_SetVCOInputRange(MICROPY_HW_CLK_PLLVCI_LL);
    LL_RCC_PLL1_SetVCOOutputRange(MICROPY_HW_CLK_PLLVCO_LL);
    LL_RCC_PLL1P_Enable();
    LL_RCC_PLL1_Enable();
    if (!WAIT_UNTIL(LL_RCC_PLL1_IsReady())) {
        goto fail;
    }

    LL_RCC_SetAHBPrescaler(LL_RCC_SYSCLK_DIV_1);
    LL_RCC_SetAPB1Prescaler(LL_RCC_APB1_DIV_1);
    LL_RCC_SetAPB2Prescaler(LL_RCC_APB2_DIV_1);
    LL_RCC_SetAPB3Prescaler(LL_RCC_APB3_DIV_1);

    // Raise the wait states and the programming delay before raising the clock.
    FLASH->ACR = (FLASH->ACR & ~(FLASH_ACR_LATENCY | FLASH_ACR_WRHIGHFREQ))
        | MICROPY_HW_FLASH_LATENCY | MCUBOOT_STM32_FLASH_PROG_DELAY;
    if (!WAIT_UNTIL((FLASH->ACR & FLASH_ACR_LATENCY) == MICROPY_HW_FLASH_LATENCY)) {
        goto fail;
    }

    LL_RCC_SetSysClkSource(LL_RCC_SYS_CLKSOURCE_PLL1);
    if (!WAIT_UNTIL(LL_RCC_GetSysClkSource() == LL_RCC_SYS_CLKSOURCE_STATUS_PLL1)) {
        LL_RCC_SetSysClkSource(LL_RCC_SYS_CLKSOURCE_HSI);
        (void)WAIT_UNTIL(LL_RCC_GetSysClkSource() == LL_RCC_SYS_CLKSOURCE_STATUS_HSI);
        goto fail;
    }
    s_hclk_hz = PLL_HCLK_HZ;
    return true;

fail:
    LL_RCC_PLL1_Disable();
    LL_RCC_HSE_Disable();
    LL_RCC_HSE_DisableBypass();
    FLASH->ACR = s_entry.flash_acr;
    PWR->VOSCR = s_entry.voscr;
    return false;
}

#else

// The main PLL at the board clock (MICROPY_HW_CLK_PLL* in mpconfigboard.h), with the bus
// dividers of ports/stm32/powerctrl.c and mboot: AHB 1, APB1 4, APB2 2. The USB clock is the
// PLLQ output (RCC_DCKCFGR2.CK48MSEL stays 0), so it has to be 48 MHz.
#define PLL_VCO_HZ (PLL_SOURCE_HZ / MICROPY_HW_CLK_PLLM * MICROPY_HW_CLK_PLLN)
#if PLL_VCO_HZ / MICROPY_HW_CLK_PLLQ != 48000000
#error "the USB clock of the bootloader is MICROPY_HW_CLK_PLLQ of the board PLL and has to be 48 MHz"
#endif
#define PLL_APB1_DIV (4)

// The order of the SystemClock_Config() of ports/stm32/mboot for STM32F7 (voltage scale, HSE,
// PLL, flash latency, switch), with bounded waits. There is no wait for PWR_CSR1.VOSRDY: the
// voltage scale takes effect only while the PLL is on, so the flag is not set before it, and
// the 144 MHz of the board is within what the scale 3 that applies until then allows. Returns
// false, with the oscillators off and the registers as at entry, when the oscillator or the PLL
// does not become ready; the bootloader then stays on the reset clock (HSI), without USB.
static bool clock_pll_start(void) {
    RCC->APB1ENR |= RCC_APB1ENR_PWREN;
    (void)RCC->APB1ENR;
    // Voltage scaling 1, the reset value.
    PWR->CR1 |= PWR_CR1_VOS;

    #if defined(MICROPY_HW_CLK_USE_HSI) && MICROPY_HW_CLK_USE_HSI
    const uint32_t pll_source = 0;
    #else
    #if defined(MICROPY_HW_CLK_USE_BYPASS) && MICROPY_HW_CLK_USE_BYPASS
    RCC->CR |= RCC_CR_HSEBYP;
    #endif
    RCC->CR |= RCC_CR_HSEON;
    if (!WAIT_UNTIL(RCC->CR & RCC_CR_HSERDY)) {
        goto fail;
    }
    const uint32_t pll_source = RCC_PLLCFGR_PLLSRC;
    #endif

    RCC->CR &= ~RCC_CR_PLLON;
    if (!WAIT_UNTIL(!(RCC->CR & RCC_CR_PLLRDY))) {
        goto fail;
    }
    RCC->PLLCFGR = pll_source
        | (MICROPY_HW_CLK_PLLM << RCC_PLLCFGR_PLLM_Pos)
        | (MICROPY_HW_CLK_PLLN << RCC_PLLCFGR_PLLN_Pos)
        | (((MICROPY_HW_CLK_PLLP >> 1) - 1) << RCC_PLLCFGR_PLLP_Pos)
        | (MICROPY_HW_CLK_PLLQ << RCC_PLLCFGR_PLLQ_Pos)
        #if defined(RCC_PLLCFGR_PLLR)
        | (2 << RCC_PLLCFGR_PLLR_Pos)
        #endif
    ;
    RCC->CR |= RCC_CR_PLLON;
    if (!WAIT_UNTIL(RCC->CR & RCC_CR_PLLRDY)) {
        goto fail;
    }

    // Raise the wait states before raising the clock, and set the bus dividers before the
    // switch so that no bus exceeds its limit.
    FLASH->ACR = (FLASH->ACR & ~FLASH_ACR_LATENCY) | MICROPY_HW_FLASH_LATENCY;
    if (!WAIT_UNTIL((FLASH->ACR & FLASH_ACR_LATENCY) == MICROPY_HW_FLASH_LATENCY)) {
        goto fail;
    }
    RCC->CFGR = (RCC->CFGR & ~(RCC_CFGR_HPRE | RCC_CFGR_PPRE1 | RCC_CFGR_PPRE2))
        | RCC_CFGR_HPRE_DIV1 | RCC_CFGR_PPRE1_DIV4 | RCC_CFGR_PPRE2_DIV2;

    RCC->CFGR = (RCC->CFGR & ~RCC_CFGR_SW) | RCC_CFGR_SW_PLL;
    if (!WAIT_UNTIL((RCC->CFGR & RCC_CFGR_SWS) == RCC_CFGR_SWS_PLL)) {
        RCC->CFGR &= ~RCC_CFGR_SW;
        (void)WAIT_UNTIL((RCC->CFGR & RCC_CFGR_SWS) == RCC_CFGR_SWS_HSI);
        goto fail;
    }
    s_hclk_hz = PLL_HCLK_HZ;
    s_uart_hz = PLL_HCLK_HZ / PLL_APB1_DIV;
    return true;

fail:
    RCC->CR &= ~RCC_CR_PLLON;
    RCC->CR &= ~RCC_CR_HSEON;
    RCC->CR &= ~RCC_CR_HSEBYP;
    RCC->CFGR = s_entry.cfgr;
    RCC->PLLCFGR = s_entry.pllcfgr;
    FLASH->ACR = s_entry.flash_acr;
    PWR->CR1 = s_entry.pwr_cr1;
    return false;
}

#endif

#else

static bool clock_pll_start(void) {
    return false;
}

#endif

// Puts the clock tree back in the state a reset leaves, for the application.
static void clock_restore(void) {
    #if defined(STM32F7)
    if (s_pll_active) {
        RCC->CFGR &= ~RCC_CFGR_SW;
        (void)WAIT_UNTIL((RCC->CFGR & RCC_CFGR_SWS) == RCC_CFGR_SWS_HSI);
        FLASH->ACR = s_entry.flash_acr;
        RCC->CR &= ~RCC_CR_PLLON;
        (void)WAIT_UNTIL(!(RCC->CR & RCC_CR_PLLRDY));
        RCC->CR &= ~RCC_CR_HSEON;
        (void)WAIT_UNTIL(!(RCC->CR & RCC_CR_HSERDY));
        RCC->CR &= ~RCC_CR_HSEBYP;
        RCC->CFGR = s_entry.cfgr;
        RCC->PLLCFGR = s_entry.pllcfgr;
        RCC->DCKCFGR2 = s_entry.dckcfgr2;
        PWR->CR1 = s_entry.pwr_cr1;
        s_pll_active = false;
        s_hclk_hz = HSI_VALUE;
        s_uart_hz = HSI_VALUE;
    }
    #else
    if (s_pll_active) {
        LL_RCC_SetSysClkSource(LL_RCC_SYS_CLKSOURCE_HSI);
        (void)WAIT_UNTIL(LL_RCC_GetSysClkSource() == LL_RCC_SYS_CLKSOURCE_STATUS_HSI);
        FLASH->ACR = s_entry.flash_acr;
        LL_RCC_PLL1_Disable();
        (void)WAIT_UNTIL(!LL_RCC_PLL1_IsReady());
        LL_RCC_HSE_Disable();
        (void)WAIT_UNTIL(!LL_RCC_HSE_IsReady());
        LL_RCC_HSE_DisableBypass();
        RCC->PLL1CFGR = 0;
        RCC->PLL1DIVR = 0x01010280U;
        RCC->PLL1FRACR = 0;
        PWR->VOSCR = s_entry.voscr;
        (void)WAIT_UNTIL(PWR->VOSSR & PWR_VOSSR_VOSRDY);
        s_pll_active = false;
        s_hclk_hz = HSI_VALUE >> ((RCC->CR & RCC_CR_HSIDIV) >> RCC_CR_HSIDIV_Pos);
    }
    #endif
}

// ---- timing ----

void SysTick_Handler(void) {
    s_ticks++;
}

uint32_t mcuboot_port_ticks_ms(void) {
    return s_ticks;
}

// The time base of the HAL flash driver (flash.c).
uint32_t HAL_GetTick(void) {
    return mcuboot_port_ticks_ms();
}

void mcuboot_port_delay_ms(uint32_t ms) {
    if (!(SysTick->CTRL & SysTick_CTRL_ENABLE_Msk)) {
        // The tick is stopped (before mcuboot_port_early_init() or after
        // mcuboot_port_deinit()): count loop iterations instead.
        for (volatile uint32_t n = ms * (s_hclk_hz / 4000); n != 0; n--) {
        }
        return;
    }
    uint32_t t0 = s_ticks;
    while ((uint32_t)(s_ticks - t0) < ms) {
    }
}

// ---- lifecycle ----

void mcuboot_port_early_init(void) {
    #if defined(STM32H5)
    s_entry.ahb2enr = RCC->AHB2ENR;
    s_entry.apb1lenr = RCC->APB1LENR;
    s_entry.apb2enr = RCC->APB2ENR;
    s_entry.apb3enr = RCC->APB3ENR;
    s_entry.flash_acr = FLASH->ACR;
    s_entry.voscr = PWR->VOSCR;
    s_entry.dbpcr = PWR->DBPCR;
    #else
    s_entry.ahb1enr = RCC->AHB1ENR;
    s_entry.ahb3enr = RCC->AHB3ENR;
    s_entry.apb1enr = RCC->APB1ENR;
    s_entry.apb2enr = RCC->APB2ENR;
    s_entry.cfgr = RCC->CFGR;
    s_entry.pllcfgr = RCC->PLLCFGR;
    s_entry.dckcfgr2 = RCC->DCKCFGR2;
    s_entry.flash_acr = FLASH->ACR;
    // The PWR registers read as zero until the interface clock runs.
    RCC->APB1ENR |= RCC_APB1ENR_PWREN;
    (void)RCC->APB1ENR;
    s_entry.pwr_cr1 = PWR->CR1;
    #endif
    s_rsr = MCUBOOT_STM32_RCC_RESET_FLAGS;

    for (size_t i = 0; i < 3; i++) {
        mcuboot_stm32_uid[i] = ((volatile uint32_t *)UID_BASE)[i];
    }

    SCB->VTOR = (uint32_t)g_pfnVectors;
    SCB->SHCSR |= SCB_SHCSR_MEMFAULTENA_Msk | SCB_SHCSR_BUSFAULTENA_Msk | SCB_SHCSR_USGFAULTENA_Msk;
    #if defined(STM32H5)
    SCB->SHCSR |= SCB_SHCSR_SECUREFAULTENA_Msk;
    #endif

    mcuboot_stm32_backup_access();
    mboot_board_hook();

    #if defined(STM32H5)
    uint32_t hsidiv = (RCC->CR & RCC_CR_HSIDIV) >> RCC_CR_HSIDIV_Pos;
    s_hclk_hz = HSI_VALUE >> hsidiv;
    #else
    s_hclk_hz = HSI_VALUE;
    s_uart_hz = s_hclk_hz;
    #endif
    s_pll_active = clock_pll_start();
    #if defined(STM32F7)
    SystemCoreClock = s_hclk_hz;
    #endif

    #if MCUBOOT_STM32_ICACHE
    #if defined(STM32H5)
    ICACHE->CR |= ICACHE_CR_EN;
    #else
    SCB_EnableICache();
    SCB_EnableDCache();
    #endif
    #endif

    SysTick->LOAD = s_hclk_hz / 1000 - 1;
    SysTick->VAL = 0;
    NVIC_SetPriority(SysTick_IRQn, (1u << __NVIC_PRIO_BITS) - 1);
    SysTick->CTRL = SysTick_CTRL_CLKSOURCE_Msk | SysTick_CTRL_TICKINT_Msk | SysTick_CTRL_ENABLE_Msk;

    gpio_config(MCUBOOT_PIN_GPIO(MICROPY_HW_LED1), MCUBOOT_PIN_NUMBER(MICROPY_HW_LED1), GPIO_MODE_OUT, 0, 0);
    gpio_config(MCUBOOT_PIN_GPIO(MICROPY_HW_LED2), MCUBOOT_PIN_NUMBER(MICROPY_HW_LED2), GPIO_MODE_OUT, 0, 0);
    gpio_config(MCUBOOT_PIN_GPIO(MICROPY_HW_LED3), MCUBOOT_PIN_NUMBER(MICROPY_HW_LED3), GPIO_MODE_OUT, 0, 0);
    mcuboot_port_led(0);

    #if MCUBOOT_LOG_LEVEL > 0
    uart_init();
    #endif

    s_started = true;
}

void mcuboot_port_deinit(void) {
    if (!s_started) {
        return;
    }
    s_started = false;

    #if MCUBOOT_DFU_ENABLE
    mcuboot_port_usb_deinit();
    #endif

    __disable_irq();

    // Reaching the end of the bootloader run means the recovery path was not needed.
    MCUBOOT_STM32_BKP_FAULTS = 0;

    SysTick->CTRL = 0;
    SCB->ICSR = SCB_ICSR_PENDSTCLR_Msk;
    for (size_t i = 0; i < NVIC_WORDS; i++) {
        NVIC->ICER[i] = 0xFFFFFFFFu;
        NVIC->ICPR[i] = 0xFFFFFFFFu;
    }

    #if defined(STM32H5)
    // The flash controller is locked by the flash driver after every operation; wait for an
    // erase that may still run.
    if (!WAIT_UNTIL(!(FLASH->NSSR & FLASH_SR_BSY))) {
        MCUBOOT_LOG_ERR("flash controller still busy, an operation is still running");
    }
    FLASH->NSCR |= FLASH_CR_LOCK;

    // Disable the cache before the application starts. The Cortex-M33 has no data cache, so the
    // instruction cache is the only one.
    if (mcuboot_stm32_icache_invalidate() != 0) {
        MCUBOOT_LOG_ERR("instruction cache invalidation timed out");
    }
    ICACHE->CR &= ~ICACHE_CR_EN;

    // Peripherals used by the bootloader back to their reset state, then their clocks.
    GPIO_RSTR = s_gpio_used;
    GPIO_RSTR = 0;
    RCC->APB1LRSTR = RCC_APB1LRSTR_USART3RST;
    RCC->APB1LRSTR = 0;
    RCC->AHB2ENR = s_entry.ahb2enr;
    RCC->APB1LENR = s_entry.apb1lenr;
    RCC->APB2ENR = s_entry.apb2enr;
    RCC->APB3ENR = s_entry.apb3enr;
    PWR->DBPCR = s_entry.dbpcr;
    s_uart_ready = false;

    SCB->SHCSR &= ~(SCB_SHCSR_MEMFAULTENA_Msk | SCB_SHCSR_BUSFAULTENA_Msk | SCB_SHCSR_USGFAULTENA_Msk
        | SCB_SHCSR_SECUREFAULTENA_Msk);

    clock_restore();
    #else
    // The flash controller is locked by the flash driver after every operation; wait for an
    // erase that may still run.
    if (!WAIT_UNTIL(!(FLASH->SR & FLASH_SR_BSY))) {
        MCUBOOT_LOG_ERR("flash controller still busy, an operation is still running");
    }
    FLASH->CR |= FLASH_CR_LOCK;

    // Write the data cache back (it holds the request region and everything else the bootloader
    // wrote) and disable both caches before the application starts. The disable cleans and
    // invalidates the data cache.
    SCB_DisableICache();
    SCB_DisableDCache();

    // Peripherals used by the bootloader back to their reset state, then their clocks. The
    // clock restore uses the PWR clock and so comes before the clock enables are put back.
    GPIO_RSTR = s_gpio_used;
    GPIO_RSTR = 0;
    // The board hook sets state that is meant to outlive the bootloader (see mboot_board_hook()).
    mboot_board_hook();
    RCC->APB1RSTR = RCC_APB1RSTR_USART3RST;
    RCC->APB1RSTR = 0;
    #if defined(MBOOT_SPIFLASH_ADDR) && defined(RCC_AHB3RSTR_QSPIRST)
    // The early init of the board may have set up the QUADSPI of a second SPI flash.
    RCC->AHB3RSTR = RCC_AHB3RSTR_QSPIRST;
    RCC->AHB3RSTR = 0;
    #endif
    s_uart_ready = false;

    SCB->SHCSR &= ~(SCB_SHCSR_MEMFAULTENA_Msk | SCB_SHCSR_BUSFAULTENA_Msk | SCB_SHCSR_USGFAULTENA_Msk);

    clock_restore();
    PWR->CR1 = s_entry.pwr_cr1;
    RCC->AHB1ENR = s_entry.ahb1enr;
    RCC->AHB3ENR = s_entry.ahb3enr;
    RCC->APB1ENR = s_entry.apb1enr;
    RCC->APB2ENR = s_entry.apb2enr;
    #endif
}

uint32_t mcuboot_port_reset_cause(void) {
    uint32_t cause = 0;
    if (s_rsr & MCUBOOT_STM32_RSTF_POR) {
        cause |= MCUBOOT_RESET_POR;
    }
    if (s_rsr & MCUBOOT_STM32_RSTF_SOFT) {
        cause |= MCUBOOT_RESET_SOFT;
    }
    if (s_rsr & MCUBOOT_STM32_RSTF_WDT) {
        cause |= MCUBOOT_RESET_WDT;
    }
    if (s_rsr & MCUBOOT_STM32_RSTF_LPWR) {
        cause |= MCUBOOT_RESET_OTHER;
    }
    // The NRST pin flag is set by every reset on this part, so it only identifies the cause
    // when nothing else does.
    if (cause == 0) {
        cause = (s_rsr & MCUBOOT_STM32_RSTF_PIN) ? MCUBOOT_RESET_PIN : MCUBOOT_RESET_OTHER;
    }
    // The application finds the reset flags cleared, so it gets them through a backup register.
    mcuboot_stm32_reset_flags_stash(s_rsr);
    MCUBOOT_STM32_RESET_FLAGS_CLEAR();
    return cause;
}

// ---- recovery inputs ----

static uint32_t fault_count(void) {
    uint32_t v = MCUBOOT_STM32_BKP_FAULTS;
    return (v & MCUBOOT_STM32_FAULTS_KEY_MASK) == MCUBOOT_STM32_FAULTS_KEY ? v & 0xFFFFu : 0;
}

uint32_t mcuboot_port_fault_resets(void) {
    return fault_count();
}

bool mcuboot_port_entry_forced(void) {
    // USER button: sampled for 10 ms, and every sample must read pressed.
    gpio_config(MCUBOOT_PIN_GPIO(MICROPY_HW_USRSW_PIN), MCUBOOT_PIN_NUMBER(MICROPY_HW_USRSW_PIN), GPIO_MODE_IN, 0, MICROPY_HW_USRSW_PULL);
    for (int i = 0; i < 5; i++) {
        mcuboot_port_delay_ms(2);
        bool pressed = ((MCUBOOT_PIN_GPIO(MICROPY_HW_USRSW_PIN)->IDR >> MCUBOOT_PIN_NUMBER(MICROPY_HW_USRSW_PIN)) & 1u) == MICROPY_HW_USRSW_PRESSED;
        if (!pressed) {
            return false;
        }
    }
    return true;
}

// ---- faults ----
//
// Every fault of the bootloader, and an NMI that is not a flash ECC event, logs one line and
// resets. Consecutive resets are counted in a backup register and reported by
// mcuboot_port_fault_resets(). After 3 of them the main flow goes to recovery (DFU) instead of
// repeating the fault. mcuboot_port_deinit() clears the count.

static void fault_hex(uint32_t v) {
    char b[8];
    for (int i = 0; i < 8; i++) {
        uint32_t d = (v >> (28 - 4 * i)) & 0xFu;
        b[i] = d < 10 ? '0' + d : 'A' + d - 10;
    }
    mcuboot_port_log_write(b, 8);
}

static void fault_text(const char *s) {
    size_t n = 0;
    while (s[n] != 0) {
        n++;
    }
    mcuboot_port_log_write(s, n);
}

__attribute__((used, noreturn)) static void fault_report(uint32_t id, uint32_t pc) {
    fault_text("\nFAULT ");
    fault_hex(id);
    fault_text(" pc=");
    fault_hex(pc);
    fault_text(" cfsr=");
    fault_hex(SCB->CFSR);
    fault_text(" hfsr=");
    fault_hex(SCB->HFSR);
    fault_text(" bfar=");
    fault_hex(SCB->BFAR);
    #if MCUBOOT_STM32_FLASH_ECC
    fault_text(" eccd=");
    fault_hex(FLASH->ECCDETR);
    #endif
    fault_text("\n");

    uint32_t n = fault_count() + 1;
    MCUBOOT_STM32_BKP_FAULTS = MCUBOOT_STM32_FAULTS_KEY | (n < 0xFFFFu ? n : 0xFFFFu);
    mcuboot_port_reset();
}

__attribute__((used)) void fault_c(const uint32_t *frame, uint32_t id) {
    // The stacked PC is frame[6]. If the stack itself is unusable the value is not trusted and
    // is only logged.
    fault_report(id, frame[6]);
}

#define FAULT_ENTRY(name, id) \
    __attribute__((naked)) void name(void) { \
        __asm volatile ( \
    "tst lr, #4\n" \
    "ite eq\n" \
    "mrseq r0, msp\n" \
    "mrsne r0, psp\n" \
    "movs r1, %0\n" \
    "b fault_c\n" \
    : : "i" (id)); \
    }

FAULT_ENTRY(HardFault_Handler, 3)
FAULT_ENTRY(MemManage_Handler, 4)
FAULT_ENTRY(BusFault_Handler, 5)
FAULT_ENTRY(UsageFault_Handler, 6)
#if defined(STM32H5)
FAULT_ENTRY(SecureFault_Handler, 7)
#endif

// On STM32H5 a flash double ECC error raises the NMI and the load that caused it still returns
// data. The flash functions detect the error through mcuboot_port_ecc_events(). Anything else
// that raises the NMI is treated as a fault.
void NMI_Handler(void) {
    #if MCUBOOT_STM32_FLASH_ECC
    if (mcuboot_stm32_nmi_ecc()) {
        return;
    }
    #endif
    fault_report(2, 0);
}
