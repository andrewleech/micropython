/*
 * This file is part of the MicroPython project, http://micropython.org/
 *
 * The MIT License (MIT)
 *
 * Copyright (c) 2021 Damien P. George
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

#include "py/runtime.h"
#include "py/mphal.h"
#include "usb.h"

#if MICROPY_HW_ENABLE_USBDEV

#include "esp_mac.h"
#include "esp_rom_gpio.h"
#include "esp_private/usb_phy.h"

#include "shared/tinyusb/mp_usbd.h"

static usb_phy_handle_t phy_hdl;

void usb_phy_init(void) {
    // ref: https://github.com/espressif/esp-usb/blob/4b6a798d0bed444fff48147c8dcdbbd038e92892/device/esp_tinyusb/tinyusb.c

    // Configure USB PHY
    static const usb_phy_config_t phy_conf = {
        .controller = USB_PHY_CTRL_OTG,
        .otg_mode = USB_OTG_MODE_DEVICE,
        .target = USB_PHY_TARGET_INT,
    };

    // Init ESP USB Phy
    usb_new_phy(&phy_conf, &phy_hdl);
}

#if CONFIG_IDF_TARGET_ESP32S3 || CONFIG_IDF_TARGET_ESP32P4
void usb_usj_mode(void) {
    // Switch the USB PHY back to Serial/Jtag mode, disabling OTG support
    // This should be run before jumping to bootloader.
    usb_del_phy(phy_hdl);
    usb_phy_config_t phy_conf = {
        .controller = USB_PHY_CTRL_SERIAL_JTAG,
    };
    usb_new_phy(&phy_conf, &phy_hdl);
}
#endif

void mp_usbd_port_get_serial_number(char *serial_buf) {
    // use factory default MAC as serial ID
    uint8_t mac[8];
    esp_efuse_mac_get_default(mac);
    MP_STATIC_ASSERT(sizeof(mac) * 2 <= MICROPY_HW_USB_DESC_STR_MAX);
    mp_usbd_hex_str(serial_buf, mac, sizeof(mac));
}

#endif // MICROPY_HW_ENABLE_USBDEV

#if MICROPY_HW_USB_HOST

#include "esp_private/usb_phy.h"
#include "py/mphal.h"

static usb_phy_handle_t phy_hdl_host;

void usb_phy_init_host(void) {
    // Skip if already initialized (e.g., after soft reset).
    if (phy_hdl_host != NULL) {
        return;
    }

    // Configure USB PHY for host mode.
    static const usb_phy_config_t phy_conf = {
        .controller = USB_PHY_CTRL_OTG,
        .otg_mode = USB_OTG_MODE_HOST,
        .target = USB_PHY_TARGET_INT,
    };

    // Init ESP USB Phy for host mode.
    usb_new_phy(&phy_conf, &phy_hdl_host);
}

void usb_phy_deinit_host(void) {
    if (phy_hdl_host) {
        usb_del_phy(phy_hdl_host);
        phy_hdl_host = NULL;
    }
}

// Provide tusb_time_millis_api for TinyUSB host timing.
uint32_t tusb_time_millis_api(void) {
    return mp_hal_ticks_ms();
}

// Initialize USB hardware for host mode (called from mp_usbh.c).
void mp_usbh_ll_init_vbus_fs(void) {
    usb_phy_init_host();
}

// USB host interrupt enable/disable.
// On ESP32, hcd_int_disable() calls esp_intr_free() which deallocates the
// interrupt entirely rather than just masking it. Since there's no public API
// to access TinyUSB's internal interrupt handle for esp_intr_disable/enable,
// these are no-ops. The interrupt remains allocated from tuh_init() and
// mp_usbh_task() checks the active flag before processing events.
void mp_usbh_int_enable(void) {
    // No-op: interrupt stays allocated from tuh_init().
}

void mp_usbh_int_disable(void) {
    // No-op: cannot disable without deallocating. See comment above.
}

#endif // MICROPY_HW_USB_HOST
