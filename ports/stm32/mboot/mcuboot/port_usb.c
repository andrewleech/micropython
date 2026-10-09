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

// USB device bring-up of the bootloader (mboot_port_usb_*), as the application does it.
//
// STM32H5: the full-speed USB DRD controller on PA11/PA12 with the 48 MHz clock from HSI48,
// trimmed by the CRS from the USB start-of-frame packets. This is the configuration the
// application uses when MICROPY_HW_CLK_USE_PLL3_FOR_USB is not set.
//
// STM32F7: the OTG_HS core (TinyUSB root hub port 1) in full-speed mode with its internal
// full-speed PHY on PB14/PB15, MICROPY_HW_USB_HS_IN_FS in the board file, with the 48 MHz clock
// from the PLLQ output of the board clock (a bootloader that stays on the HSI clock has no USB).

#include <stddef.h>

#include "port_stm32.h"
#if defined(STM32H5)
#include "stm32h5xx_ll_rcc.h"
#endif
#include "tusb.h"

#include "mcuboot_config/mcuboot_config.h"
#include "mcuboot_config/mcuboot_logging.h"
#include "mboot_log.h"

#if defined(STM32H5)
#define USB_GPIO_PORT GPIOA
#define USB_PIN_DM (11)
#define USB_PIN_DP (12)
#define USB_PIN_AF (10)
#define USB_IRQn USB_DRD_FS_IRQn
#define USB_IRQ_HANDLER USB_DRD_FS_IRQHandler
#define USB_RHPORT (0)
#else
#if !MICROPY_HW_USB_HS_IN_FS
#error "the USB device of the STM32F7 bootloader is the OTG_HS core with its internal full-speed PHY (MICROPY_HW_USB_HS_IN_FS)"
#endif
#define USB_GPIO_PORT GPIOB
#define USB_PIN_DM (14)
#define USB_PIN_DP (15)
#define USB_PIN_AF (12)
#define USB_IRQn OTG_HS_IRQn
#define USB_IRQ_HANDLER OTG_HS_IRQHandler
// OTG_HS is TinyUSB root hub port 1 on STM32F4, F7 and H7.
#define USB_RHPORT (1)
#endif

#define USB_IRQ_PRIORITY (4)
#define CLOCK_WAIT_LOOPS (2000000)

// Bits of mboot_port_led().
#define MBOOT_LED_ALIVE (1u << 0)
#define MBOOT_LED_ERROR (1u << 2)

#if defined(STM32H5)
// The USB clock is 48 MHz and the CRS synchronises to the 1 kHz start-of-frame.
#define CRS_RELOAD ((48000000u / 1000u) - 1u)
#define CRS_FELIM (0x22u)
#define CRS_TRIM_DEFAULT (0x20u)
#endif

static bool s_usb_up;

#if defined(STM32F7)

int mboot_port_usb_init(void) {
    if (!(RCC->CR & RCC_CR_PLLRDY)) {
        MCUBOOT_LOG_ERR("the PLL is not running, USB is not available");
        mboot_port_led(MBOOT_LED_ALIVE | MBOOT_LED_ERROR);
        return -ETIMEDOUT;
    }
    // The 48 MHz clock is the PLLQ output (the reset selection, set again in case the state
    // before the bootloader differs).
    RCC->DCKCFGR2 &= ~RCC_DCKCFGR2_CK48MSEL;

    mboot_stm32_gpio_af(USB_GPIO_PORT, USB_PIN_DM, USB_PIN_AF);
    mboot_stm32_gpio_af(USB_GPIO_PORT, USB_PIN_DP, USB_PIN_AF);

    RCC->AHB1ENR |= RCC_AHB1ENR_OTGHSEN;
    (void)RCC->AHB1ENR;

    // VBUS sensing stays off: dcd_init() of TinyUSB (called by tusb_init()) clears GCCFG.VBDEN and
    // sets the B session valid override. It chooses the VBDEN bit layout for the core GUID 0x2100
    // of the F76x OTG_HS (dwc2_stm32.h, GUID 0x2000 up to 0x4FFF), as the CMSIS header of the
    // F767 defines it.

    NVIC_SetPriority(USB_IRQn, USB_IRQ_PRIORITY);
    NVIC_EnableIRQ(USB_IRQn);
    s_usb_up = true;
    return 0;
}

#else

int mboot_port_usb_init(void) {
    LL_RCC_HSI48_Enable();
    if (!MBOOT_STM32_WAIT_UNTIL(LL_RCC_HSI48_IsReady(), CLOCK_WAIT_LOOPS)) {
        LL_RCC_HSI48_Disable();
        MCUBOOT_LOG_ERR("HSI48 did not start, USB is not available");
        mboot_port_led(MBOOT_LED_ALIVE | MBOOT_LED_ERROR);
        return -ETIMEDOUT;
    }
    LL_RCC_SetUSBClockSource(LL_RCC_USB_CLKSOURCE_HSI48);

    RCC->APB1LENR |= RCC_APB1LENR_CRSEN;
    (void)RCC->APB1LENR;
    CRS->CFGR = (2u << CRS_CFGR_SYNCSRC_Pos) | (CRS_FELIM << CRS_CFGR_FELIM_Pos) | (CRS_RELOAD << CRS_CFGR_RELOAD_Pos);
    CRS->CR = (CRS_TRIM_DEFAULT << CRS_CR_TRIM_Pos) | CRS_CR_AUTOTRIMEN | CRS_CR_CEN;

    mboot_stm32_gpio_af(USB_GPIO_PORT, USB_PIN_DM, USB_PIN_AF);
    mboot_stm32_gpio_af(USB_GPIO_PORT, USB_PIN_DP, USB_PIN_AF);

    RCC->APB2ENR |= RCC_APB2ENR_USBEN;
    (void)RCC->APB2ENR;
    PWR->USBSCR |= PWR_USBSCR_USB33SV;

    NVIC_SetPriority(USB_IRQn, USB_IRQ_PRIORITY);
    NVIC_EnableIRQ(USB_IRQn);
    s_usb_up = true;
    return 0;
}

#endif

void mboot_port_usb_deinit(void) {
    if (!s_usb_up) {
        return;
    }
    s_usb_up = false;

    NVIC_DisableIRQ(USB_IRQn);
    if (tud_inited()) {
        // Remove the pull-up so the host sees the device leave.
        tud_disconnect();
    }
    NVIC_ClearPendingIRQ(USB_IRQn);

    #if defined(STM32F7)
    RCC->AHB1RSTR |= RCC_AHB1RSTR_OTGHRST;
    RCC->AHB1RSTR &= ~RCC_AHB1RSTR_OTGHRST;
    RCC->AHB1ENR &= ~RCC_AHB1ENR_OTGHSEN;
    #else
    RCC->APB2RSTR |= RCC_APB2RSTR_USBRST;
    RCC->APB2RSTR &= ~RCC_APB2RSTR_USBRST;
    RCC->APB2ENR &= ~RCC_APB2ENR_USBEN;
    PWR->USBSCR &= ~PWR_USBSCR_USB33SV;

    CRS->CR = 0;
    RCC->APB1LRSTR |= RCC_APB1LRSTR_CRSRST;
    RCC->APB1LRSTR &= ~RCC_APB1LRSTR_CRSRST;
    RCC->APB1LENR &= ~RCC_APB1LENR_CRSEN;

    LL_RCC_SetUSBClockSource(LL_RCC_USB_CLKSOURCE_NONE);
    LL_RCC_HSI48_Disable();
    #endif
}

void mboot_port_usb_serial_number(char *buf, size_t len) {
    static const char hex[] = "0123456789ABCDEF";
    if (len == 0) {
        return;
    }
    size_t n = 0;
    for (size_t w = 0; w < 3; w++) {
        for (size_t i = 0; i < 8 && n + 1 < len; i++) {
            buf[n++] = hex[(mboot_stm32_uid[w] >> (28 - 4 * i)) & 0xFu];
        }
    }
    buf[n] = '\0';
}

void USB_IRQ_HANDLER(void) {
    tusb_int_handler(USB_RHPORT, true);
}

// TinyUSB without an OS gets its time base from the port.
uint32_t tusb_time_millis_api(void) {
    return mboot_port_ticks_ms();
}
