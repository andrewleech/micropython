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

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "mpconfigboard.h"
#include "mboot_led.h"

#if defined(MBOOT_LED1_GPIO)
#include "fsl_gpio.h"
#include "fsl_iomuxc.h"

#define MBOOT_LED_DESCRIPTOR(_n) \
    { MBOOT_LED##_n##_GPIO, MBOOT_LED##_n##_PIN, { MBOOT_LED##_n##_MUX }, \
      MBOOT_LED##_n##_PAD_CONFIG, MBOOT_LED##_n##_ACTIVE_LOW }

typedef struct {
    uint32_t mux_register;
    uint32_t mux_mode;
    uint32_t input_register;
    uint32_t input_daisy;
    uint32_t config_register;
} mboot_led_mux_t;

typedef struct {
    GPIO_Type *gpio;
    uint32_t pin;
    mboot_led_mux_t mux;
    uint32_t pad_config;
    bool active_low;
} mboot_led_t;

static const mboot_led_t mboot_leds[] = {
    MBOOT_LED_DESCRIPTOR(1),
    #if defined(MBOOT_LED2_GPIO)
    MBOOT_LED_DESCRIPTOR(2),
    #endif
    #if defined(MBOOT_LED3_GPIO)
    MBOOT_LED_DESCRIPTOR(3),
    #endif
};

#define MBOOT_LED_COUNT (sizeof(mboot_leds) / sizeof(mboot_leds[0]))
#define MBOOT_LED_ALIVE_MS (50u)

static uint32_t mboot_led_mask;
static uint32_t mboot_led_alive_ms;
static bool mboot_led_alive_on;
static bool mboot_led_initialized;

static void mboot_led_write(size_t index, bool on) {
    const mboot_led_t *led = &mboot_leds[index];
    GPIO_PinWrite(led->gpio, led->pin, on != led->active_low);
}

static void mboot_led_init(void) {
    if (mboot_led_initialized) {
        return;
    }
    for (size_t i = 0; i < MBOOT_LED_COUNT; ++i) {
        const mboot_led_t *led = &mboot_leds[i];
        gpio_pin_config_t config = { kGPIO_DigitalOutput, led->active_low ? 1u : 0u, kGPIO_NoIntmode };
        IOMUXC_SetPinMux(led->mux.mux_register, led->mux.mux_mode,
            led->mux.input_register, led->mux.input_daisy, led->mux.config_register, 0U);
        IOMUXC_SetPinConfig(led->mux.mux_register, led->mux.mux_mode,
            led->mux.input_register, led->mux.input_daisy, led->mux.config_register, led->pad_config);
        GPIO_PinInit(led->gpio, led->pin, &config);
    }
    mboot_led_initialized = true;
}

void mboot_led_set(uint32_t mask) {
    bool alive_active = (mask & 1u) != 0;
    if (!alive_active) {
        mboot_led_alive_ms = 0;
        mboot_led_alive_on = false;
    }
    mboot_led_init();
    mboot_led_mask = mask;
    for (size_t i = 0; i < MBOOT_LED_COUNT; ++i) {
        bool on = (mask & (1u << i)) != 0;
        if (i == 0 && alive_active) {
            on = mboot_led_alive_on;
        }
        mboot_led_write(i, on);
    }
}

void mboot_led_tick_1ms(void) {
    if (!mboot_led_initialized || (mboot_led_mask & 1u) == 0) {
        return;
    }
    if (++mboot_led_alive_ms == MBOOT_LED_ALIVE_MS) {
        mboot_led_alive_ms = 0;
        mboot_led_alive_on = !mboot_led_alive_on;
        mboot_led_write(0, mboot_led_alive_on);
    }
}
#else

void mboot_led_set(uint32_t mask) {
    (void)mask;
}

void mboot_led_tick_1ms(void) {
}
#endif
