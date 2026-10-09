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

// Minimal test application for the hardware power-cut sweeps (tests/mboot/fi_sweep.py).
//
// It prints "[APP] v<major>.<minor>.<revision>" on USART3 (PD8, the ST-LINK virtual com port of
// the NUCLEO-H563ZI) once and then idles. The version comes from -DAPP_MAJOR/MINOR/REV; the
// image size is set by the pad array of -DPAD_BYTES, which is filled from pad.bin. It never
// touches flash and never confirms itself, so an unconfirmed image reverts at the next reset.
//
// The clock is the reset clock the bootloader hands over: HSI divided by the HSIDIV field of
// RCC_CR, USART3 on PCLK1 (reset selection).

#include <stdint.h>

#define REG(a) (*(volatile uint32_t *)(a))

#define RCC_CR REG(0x44020C00u)
#define RCC_AHB2ENR REG(0x44020C8Cu)
#define RCC_APB1LENR REG(0x44020C9Cu)
#define GPIOD_MODER REG(0x42020C00u)
#define GPIOD_AFRH REG(0x42020C24u)
#define USART3_CR1 REG(0x40004800u)
#define USART3_BRR REG(0x4000480Cu)
#define USART3_ISR REG(0x4000481Cu)
#define USART3_TDR REG(0x40004828u)
#define USART3_PRESC REG(0x4000482Cu)

#define STACK_TOP (0x20010000u)

#ifndef PAD_BYTES
#define PAD_BYTES 16
#endif

extern const uint8_t app_pad[PAD_BYTES];

__asm__ (
    ".section .pad,\"a\"\n"
    ".global app_pad\n"
    "app_pad:\n"
    ".incbin \"" PAD_FILE "\"\n"
    ".previous\n");

static void put_char(char c) {
    while (!(USART3_ISR & (1u << 7))) {
    }
    USART3_TDR = (uint8_t)c;
}

static void put_str(const char *s) {
    while (*s) {
        put_char(*s++);
    }
}

static void put_num(unsigned n) {
    char b[10];
    int i = 0;
    do {
        b[i++] = (char)('0' + n % 10);
        n /= 10;
    } while (n);
    while (i) {
        put_char(b[--i]);
    }
}

void Reset_Handler(void) {
    RCC_AHB2ENR |= (1u << 3);
    RCC_APB1LENR |= (1u << 18);
    (void)RCC_APB1LENR;
    GPIOD_MODER = (GPIOD_MODER & ~(3u << 16)) | (2u << 16);
    GPIOD_AFRH = (GPIOD_AFRH & ~(0xFu << 0)) | (7u << 0);
    uint32_t hclk = 64000000u >> ((RCC_CR >> 3) & 3u);
    USART3_CR1 = 0;
    USART3_PRESC = 0;
    USART3_BRR = (hclk + 115200u / 2) / 115200u;
    USART3_CR1 = (1u << 3) | 1u;
    put_str("\r\n[APP] v");
    put_num(APP_MAJOR);
    put_char('.');
    put_num(APP_MINOR);
    put_char('.');
    put_num(APP_REV);
    put_str(" pad ");
    put_num(app_pad[0]);
    put_str("\r\n");
    for (;;) {
        __asm__ volatile ("wfi");
    }
}

void Default_Handler(void) {
    for (;;) {
    }
}

typedef void (*vector_t)(void);

// Stack pointer, reset and the exception vectors up to SysTick; faults loop in Default_Handler.
__attribute__((section(".isr_vector"), used))
const vector_t vector_table[16] = {
    (vector_t)STACK_TOP,
    Reset_Handler,
    Default_Handler, // NMI
    Default_Handler, // HardFault
    Default_Handler, // MemManage
    Default_Handler, // BusFault
    Default_Handler, // UsageFault
    0, 0, 0, 0,
    Default_Handler, // SVCall
    Default_Handler, // DebugMon
    0,
    Default_Handler, // PendSV
    Default_Handler, // SysTick
};
