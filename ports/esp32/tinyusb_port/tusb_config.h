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

#ifndef _ESP32_TUSB_CONFIG_H_
#define _ESP32_TUSB_CONFIG_H_

#ifdef __cplusplus
extern "C" {
#endif

// Include ESP-IDF sdkconfig for target detection.
#include "sdkconfig.h"

// Include full MicroPython configuration chain.
// This brings in mpconfigport.h -> mpconfigboard.h
#include "py/mpconfig.h"

// TinyUSB debug logging for USB host enumeration debugging.
// Set CFG_TUSB_DEBUG to 2 to enable verbose logging, 0 to disable.
#ifndef CFG_TUSB_DEBUG
#define CFG_TUSB_DEBUG 0
#endif

#if CFG_TUSB_DEBUG
// Use standard printf for debug output (routes to ESP32 console).
#include <stdio.h>
#define CFG_TUSB_DEBUG_PRINTF printf
// Enable CDC host driver logging.
#define CFG_TUH_CDC_LOG_LEVEL 2
#endif

// CFG_TUSB_MCU is passed via CMake as CFG_TUSB_MCU=OPT_MCU_<target>

// ESP32 uses FreeRTOS - enable OS support for proper synchronization.
#define CFG_TUSB_OS             OPT_OS_FREERTOS
#define CFG_TUSB_OS_INC_PATH    freertos /

// RHPort mode configuration (required by TinyUSB).
// Port 0 is used for both device and host on ESP32.
#ifdef MICROPY_HW_USB_HOST
#if MICROPY_HW_USB_HOST
#define CFG_TUSB_RHPORT0_MODE   (OPT_MODE_HOST | OPT_MODE_FULL_SPEED)
#else
#define CFG_TUSB_RHPORT0_MODE   (OPT_MODE_DEVICE | OPT_MODE_FULL_SPEED)
#endif
#else
#define CFG_TUSB_RHPORT0_MODE   (OPT_MODE_DEVICE | OPT_MODE_FULL_SPEED)
#endif

// USB Host configuration.
#ifdef MICROPY_HW_USB_HOST
#if MICROPY_HW_USB_HOST

// DMA for ESP32 DWC2 host mode.
// ESP32-S2/S3 don't have proper L1 cache handling for DMA, disable for now.
// ESP32-P4 has cache handling in dwc2_esp32.h, so DMA can be enabled there.
#if defined(CONFIG_IDF_TARGET_ESP32P4)
#define CFG_TUH_DWC2_DMA_ENABLE  1
#else
#define CFG_TUH_DWC2_DMA_ENABLE  0
#endif

// Include host class configuration from separate header.
#include "tusb_config_host.h"

#endif // MICROPY_HW_USB_HOST value check
#endif // MICROPY_HW_USB_HOST defined check

// Include shared TinyUSB config for device-mode definitions (USBD_ITF_BUILTIN_MAX, etc.)
// This is included at the end so port-specific defines take precedence.
#include "shared/tinyusb/tusb_config.h"

#ifdef __cplusplus
}
#endif

#endif // _ESP32_TUSB_CONFIG_H_
