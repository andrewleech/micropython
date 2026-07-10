/*
 * This file is part of the MicroPython project, http://micropython.org/
 *
 * The MIT License (MIT)
 *
 * Copyright (c) 2016 Linaro Limited
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
#include <zephyr/kernel.h>
#include <zephyr/devicetree.h>
#include <zephyr/drivers/uart.h>
#include "zephyr_getchar.h"

int real_main(void);
int mp_console_init(void);

#if defined(CONFIG_USB_DEVICE_STACK_NEXT)
extern int mp_usbd_init(void);
#endif

// Check if the console device is a CDC ACM UART (USB serial).
#define MP_CONSOLE_IS_CDC_ACM DT_NODE_HAS_COMPAT(DT_CHOSEN(zephyr_console), zephyr_cdc_acm_uart)

#if MP_CONSOLE_IS_CDC_ACM && defined(CONFIG_UART_LINE_CTRL)
static void mp_wait_for_usb_dtr(void) {
    const struct device *dev = DEVICE_DT_GET(DT_CHOSEN(zephyr_console));
    uint32_t dtr = 0;

    while (!dtr) {
        uart_line_ctrl_get(dev, UART_LINE_CTRL_DTR, &dtr);
        k_msleep(100);
    }
}
#endif

int main(void) {
    #if defined(CONFIG_USB_DEVICE_STACK_NEXT)
    mp_usbd_init();
    #endif

    #if MP_CONSOLE_IS_CDC_ACM && defined(CONFIG_UART_LINE_CTRL)
    mp_wait_for_usb_dtr();
    #endif

    #ifdef CONFIG_CONSOLE_SUBSYS
    mp_console_init();
    #else
    zephyr_getchar_init();
    #endif

    real_main();

    return 0;
}
