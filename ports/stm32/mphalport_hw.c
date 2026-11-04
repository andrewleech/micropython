/*
 * This file is part of the MicroPython project, http://micropython.org/
 *
 * The MIT License (MIT)
 *
 * Copyright (c) 2013-2025 Damien P. George
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

// The parts of mphalport.c that need no MicroPython runtime: the GPIO configuration of the pin
// objects, and the HAL status conversion. The bootloader links this file too.

#include "py/mphal.h"
#include "py/mperrno.h"
#include "pin.h"

// this table converts from HAL_StatusTypeDef to POSIX errno
const byte mp_hal_status_to_errno_table[4] = {
    [HAL_OK] = 0,
    [HAL_ERROR] = MP_EIO,
    [HAL_BUSY] = MP_EBUSY,
    [HAL_TIMEOUT] = MP_ETIMEDOUT,
};

#if defined(STM32H5)
uint8_t mp_hal_unique_id_address[12];
#endif

void mp_hal_gpio_clock_enable(GPIO_TypeDef *gpio) {
    #if defined(STM32L476xx) || defined(STM32L496xx) || defined(STM32U5)
    if (gpio == GPIOG) {
        // Port G pins 2 thru 15 are powered using VddIO2 on these MCUs.
        HAL_PWREx_EnableVddIO2();
    }
    #endif

    // This logic assumes that all the GPIOx_EN bits are adjacent and ordered in one register

    #if defined(STM32F0) || defined(STM32L1)
    #define AHBxENR AHBENR
    #define AHBxENR_GPIOAEN_Pos RCC_AHBENR_GPIOAEN_Pos
    #elif defined(STM32F4) || defined(STM32F7)
    #define AHBxENR AHB1ENR
    #define AHBxENR_GPIOAEN_Pos RCC_AHB1ENR_GPIOAEN_Pos
    #elif defined(STM32H7) || defined(STM32N6)
    #define AHBxENR AHB4ENR
    #define AHBxENR_GPIOAEN_Pos RCC_AHB4ENR_GPIOAEN_Pos
    #elif defined(STM32L0)
    #define AHBxENR IOPENR
    #define AHBxENR_GPIOAEN_Pos RCC_IOPENR_IOPAEN_Pos
    #elif defined(STM32G0)
    #define AHBxENR IOPENR
    #define AHBxENR_GPIOAEN_Pos RCC_IOPENR_GPIOAEN_Pos
    #elif defined(STM32G4) || defined(STM32H5) || defined(STM32L4) || defined(STM32WB) || defined(STM32WL)
    #define AHBxENR AHB2ENR
    #define AHBxENR_GPIOAEN_Pos RCC_AHB2ENR_GPIOAEN_Pos
    #elif defined(STM32U5)
    #define AHBxENR AHB2ENR1
    #define AHBxENR_GPIOAEN_Pos RCC_AHB2ENR1_GPIOAEN_Pos
    #endif

    uint32_t gpio_idx = ((uint32_t)gpio - GPIOA_BASE) / (GPIOB_BASE - GPIOA_BASE);
    RCC->AHBxENR |= 1 << (AHBxENR_GPIOAEN_Pos + gpio_idx);
    volatile uint32_t tmp = RCC->AHBxENR; // Delay after enabling clock
    (void)tmp;
}

void mp_hal_pin_config(mp_hal_pin_obj_t pin_obj, uint32_t mode, uint32_t pull, uint32_t alt) {
    GPIO_TypeDef *gpio = pin_obj->gpio;
    uint32_t pin = pin_obj->pin;
    mp_hal_gpio_clock_enable(gpio);
    if (mode == MP_HAL_PIN_MODE_ALT || mode == MP_HAL_PIN_MODE_ALT_OPEN_DRAIN) {
        // To avoid any I/O glitches, make sure a valid alternate function is set in
        // AFR first before switching the pin mode. When switching from AF to INPUT or
        // OUTPUT, the AF in AFR will remain valid up until the pin mode is switched.
        gpio->AFR[pin >> 3] = (gpio->AFR[pin >> 3] & ~(15 << (4 * (pin & 7)))) | (alt << (4 * (pin & 7)));
    }
    gpio->MODER = (gpio->MODER & ~(3 << (2 * pin))) | ((mode & 3) << (2 * pin));
    #if defined(GPIO_ASCR_ASC0)
    // The L4 has a special analog switch to connect the GPIO to the ADC
    gpio->OTYPER = (gpio->OTYPER & ~(1 << pin)) | (((mode >> 2) & 1) << pin);
    gpio->ASCR = (gpio->ASCR & ~(1 << pin)) | ((mode >> 3) & 1) << pin;
    #else
    gpio->OTYPER = (gpio->OTYPER & ~(1 << pin)) | ((mode >> 2) << pin);
    #endif
    gpio->OSPEEDR = (gpio->OSPEEDR & ~(3 << (2 * pin))) | (2 << (2 * pin)); // full speed
    gpio->PUPDR = (gpio->PUPDR & ~(3 << (2 * pin))) | (pull << (2 * pin));
}

const pin_af_obj_t *pin_find_af(const machine_pin_obj_t *pin, uint8_t fn, uint8_t unit) {
    const pin_af_obj_t *af = pin->af;
    for (mp_uint_t i = 0; i < pin->num_af; i++, af++) {
        if (af->fn == fn && af->unit == unit) {
            return af;
        }
    }
    return NULL;
}

bool mp_hal_pin_config_alt(mp_hal_pin_obj_t pin, uint32_t mode, uint32_t pull, uint8_t fn, uint8_t unit) {
    const pin_af_obj_t *af = pin_find_af(pin, fn, unit);
    if (af == NULL) {
        return false;
    }
    mp_hal_pin_config(pin, mode, pull, af->idx);
    return true;
}

void mp_hal_pin_config_speed(mp_hal_pin_obj_t pin_obj, uint32_t speed) {
    GPIO_TypeDef *gpio = pin_obj->gpio;
    uint32_t pin = pin_obj->pin;
    gpio->OSPEEDR = (gpio->OSPEEDR & ~(3 << (2 * pin))) | (speed << (2 * pin));
}
