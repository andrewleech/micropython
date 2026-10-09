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

// Register model of the instruction cache for icache_test.c: plain memory, so the test decides
// whether the "hardware" completes an invalidation. Only what ports/stm32/mboot/mcuboot/port_stm32.h
// needs is declared.
#ifndef STM32H5_STUB_H
#define STM32H5_STUB_H

#include <stdint.h>

#define STM32H5 (1)

typedef struct {
    volatile uint32_t CR;
    volatile uint32_t SR;
    volatile uint32_t IER;
    volatile uint32_t FCR;
} ICACHE_TypeDef;

typedef struct {
    volatile uint32_t MODER;
} GPIO_TypeDef;

extern ICACHE_TypeDef fake_icache;
#define ICACHE (&fake_icache)

#define ICACHE_CR_EN (1u << 0)
#define ICACHE_CR_CACHEINV (1u << 1)
#define ICACHE_SR_BSYENDF (1u << 1)
#define ICACHE_FCR_CBSYENDF (1u << 1)

#endif
