/*
 * This file is part of the MicroPython project, http://micropython.org/
 *
 * The MIT License (MIT)
 *
 * Copyright (c) 2025 Andrew Leech
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

#include "py/mpconfig.h"

#if MICROPY_HW_USB_HOST

#include "py/mphal.h"
#include "pin.h"

// USB OTG definitions
#if !MICROPY_HW_USB_IS_MULTI_OTG
// The MCU has a single USB device-only instance
#define USB_OTG_FS USB
#endif

// TinyUSB timing API - required for USB Host operation
uint32_t tusb_time_millis_api(void) {
    return mp_hal_ticks_ms();
}

#if MICROPY_HW_USB_FS

// USB FS low-level initialization for Host mode
void mp_usbd_ll_init_fs(void) {
    // Configure USB GPIO's.
    #if defined(STM32H7)
    const uint32_t otg_alt = GPIO_AF10_OTG1_FS;
    #elif defined(STM32L0)
    const uint32_t otg_alt = GPIO_AF0_USB;
    #elif defined(STM32L432xx) || defined(STM32L452xx)
    const uint32_t otg_alt = GPIO_AF10_USB_FS;
    #elif defined(STM32H5) || defined(STM32WB)
    const uint32_t otg_alt = GPIO_AF10_USB;
    #else
    const uint32_t otg_alt = GPIO_AF10_OTG_FS;
    #endif

    mp_hal_pin_config(pin_A11, MP_HAL_PIN_MODE_ALT, MP_HAL_PIN_PULL_NONE, otg_alt);
    mp_hal_pin_config_speed(pin_A11, GPIO_SPEED_FREQ_VERY_HIGH);
    mp_hal_pin_config(pin_A12, MP_HAL_PIN_MODE_ALT, MP_HAL_PIN_PULL_NONE, otg_alt);
    mp_hal_pin_config_speed(pin_A12, GPIO_SPEED_FREQ_VERY_HIGH);

    #if defined(MICROPY_HW_USB_VBUS_DETECT_PIN)
    // Configure VBUS detect pin
    mp_hal_pin_config(MICROPY_HW_USB_VBUS_DETECT_PIN, MP_HAL_PIN_MODE_INPUT, MP_HAL_PIN_PULL_NONE, 0);
    #endif

    #if defined(MICROPY_HW_USB_OTG_ID_PIN)
    // Configure OTG ID pin for host/device detection
    mp_hal_pin_config(MICROPY_HW_USB_OTG_ID_PIN, MP_HAL_PIN_MODE_ALT, MP_HAL_PIN_PULL_UP, otg_alt);
    #endif

    // Enable USB FS Clocks
    __USB_OTG_FS_CLK_ENABLE();

    // Enable VDDUSB if required
    #if defined(STM32H5) || defined(STM32WB)
    HAL_PWREx_EnableVddUSB();
    #endif
    #if defined(STM32L4)
    HAL_PWREx_EnableVddUSB();
    #endif

    // Configure VBUS sensing and power for Host mode
    #if defined(USB_OTG_GCCFG_VBDEN)
    USB_OTG_FS->GCCFG |= USB_OTG_GCCFG_VBDEN;
    #elif defined(USB_OTG_GCCFG_VBUSASEN)
    // For Host mode, enable VBUS A-device sensing (we are the A-device/host)
    USB_OTG_FS->GCCFG |= USB_OTG_GCCFG_VBUSASEN;
    USB_OTG_FS->GCCFG &= ~USB_OTG_GCCFG_VBUSBSEN;  // Disable B-device sensing
    #if !defined(MICROPY_HW_USB_VBUS_DETECT_PIN)
    // If no VBUS detect, force valid
    USB_OTG_FS->GCCFG |= USB_OTG_GCCFG_NOVBUSSENS;
    #endif
    #endif
}

// Enable VBUS power output for Host mode
void mp_usbh_ll_init_vbus_fs(void) {
    #if defined(MICROPY_HW_USB_VBUS_POWER_PIN)
    // Enable VBUS power output pin (active high)
    mp_hal_pin_config(MICROPY_HW_USB_VBUS_POWER_PIN, MP_HAL_PIN_MODE_OUTPUT, MP_HAL_PIN_PULL_NONE, 0);
    mp_hal_pin_write(MICROPY_HW_USB_VBUS_POWER_PIN, 1);  // Enable VBUS power
    // Delay to allow VBUS to stabilize
    mp_hal_delay_ms(100);
    #endif
}

#endif // MICROPY_HW_USB_FS

#endif // MICROPY_HW_USB_HOST
