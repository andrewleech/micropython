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

// The part of the mboot_port.h implementation for STM32H5 and STM32F7 that is the same in the
// bootloader and the application: handoff words, reset and, on STM32H5, the flash ECC event
// counter.

#include <stddef.h>

#include "port_stm32.h"
#include "mcuboot_config/mcuboot_config.h"
#include "mboot_request.h"

void mboot_stm32_backup_access(void) {
    #if defined(STM32H5)
    RCC->APB3ENR |= RCC_APB3ENR_RTCAPBEN;
    PWR->DBPCR |= PWR_DBPCR_DBP;
    // Read back so the clock enable has taken effect before the first TAMP access.
    (void)RCC->APB3ENR;
    (void)PWR->DBPCR;
    #else
    // The RTC backup registers are writable once the power interface clock is on and the
    // backup domain write protection is off.
    RCC->APB1ENR |= RCC_APB1ENR_PWREN;
    (void)RCC->APB1ENR;
    PWR->CR1 |= PWR_CR1_DBP;
    (void)PWR->CR1;
    #endif
}

uint32_t mboot_port_retention_read(void) {
    mboot_stm32_backup_access();
    return MBOOT_STM32_BKP_RETENTION;
}

void mboot_port_retention_write(uint32_t v) {
    mboot_stm32_backup_access();
    MBOOT_STM32_BKP_RETENTION = v;
    (void)MBOOT_STM32_BKP_RETENTION;
}

void mboot_stm32_reset_flags_stash(uint32_t rsr) {
    mboot_stm32_backup_access();
    bool software_only = (rsr & MBOOT_STM32_RSTF_SOFT) && !(rsr & (MBOOT_STM32_RSTF_POR | MBOOT_STM32_RSTF_WDT | MBOOT_STM32_RSTF_LPWR));
    bool kept = (MBOOT_STM32_BKP_RESET_FLAGS & MBOOT_STM32_RESET_FLAGS_KEY_MASK) == MBOOT_STM32_RESET_FLAGS_KEY;
    if (software_only && kept) {
        return;
    }
    MBOOT_STM32_BKP_RESET_FLAGS = MBOOT_STM32_RESET_FLAGS_KEY | ((rsr >> 24) & 0xFFu);
    (void)MBOOT_STM32_BKP_RESET_FLAGS;
}

uint32_t mboot_stm32_reset_flags_take(uint32_t current) {
    mboot_stm32_backup_access();
    uint32_t v = MBOOT_STM32_BKP_RESET_FLAGS;
    if ((v & MBOOT_STM32_RESET_FLAGS_KEY_MASK) != MBOOT_STM32_RESET_FLAGS_KEY) {
        return current;
    }
    MBOOT_STM32_BKP_RESET_FLAGS = 0;
    return (v & 0xFFu) << 24;
}

void *mboot_port_request_ram(size_t *size_out) {
    if (size_out != NULL) {
        *size_out = MBOOT_REQ_REGION_SIZE;
    }
    return (void *)MBOOT_REQ_START;
}

MBOOT_NORETURN void mboot_port_reset(void) {
    #if defined(STM32H5)
    // The Cortex-M33 of the STM32H5 has no data cache, so the request region is in memory
    // once the stores have completed.
    __DSB();
    #else
    // The Cortex-M7 data cache holds the request region (and everything else the application
    // wrote last) until it is cleaned. The reset discards the cache, so write it back first.
    SCB_CleanDCache();
    __DSB();
    #endif
    NVIC_SystemReset();
}

// No watchdog is started by the bootloader or by this glue.
void mboot_port_wdt_feed(void) {
}

#if MBOOT_STM32_FLASH_ECC

// ---- flash ECC ----
//
// A read of a flash word with an invalid ECC returns the data and raises the NMI with
// FLASH_ECCDETR.ECCD set. The NMI handlers of the bootloader (port_core.c) and of the
// application (stm32_it.c) call mboot_stm32_nmi_ecc() and return.

volatile uint32_t mboot_ecc_nmi_count;
volatile uint32_t mboot_ecc_nmi_last;

bool mboot_stm32_nmi_ecc(void) {
    uint32_t detr = FLASH->ECCDETR;
    if (!(detr & FLASH_ECCR_ECCD)) {
        return false;
    }
    mboot_ecc_nmi_last = detr;
    mboot_ecc_nmi_count++;
    // Writing 1 clears the flag.
    FLASH->ECCDETR = FLASH_ECCR_ECCD;
    return true;
}

// A word left partly programmed by a power cut can be corrected on read instead of reported as
// a double error. FLASH_ECCCORR.ECCC is set (no NMI, the correction interrupt stays disabled)
// and the data is the erased value or a partial program. The flag is sticky, so each look that
// finds it set counts one correction and clears it.
static volatile uint32_t mboot_ecc_corrected_count;

uint32_t mboot_port_ecc_corrected(void) {
    if (FLASH->ECCCORR & FLASH_ECCR_ECCC) {
        mboot_ecc_corrected_count++;
        FLASH->ECCCORR = FLASH_ECCR_ECCC;
    }
    return mboot_ecc_corrected_count;
}

// A flag the NMI handler has not consumed yet counts as an event. That happens when the NMI is
// still pending or the caller runs in an NMI or fault handler.
uint32_t mboot_port_ecc_events(void) {
    mboot_stm32_nmi_ecc();
    return mboot_ecc_nmi_count;
}

void mboot_port_ecc_clear(void) {
    mboot_stm32_nmi_ecc();
}

#endif

#if defined(MBOOT_ROLE_APP)

// The application has no log output of its own.
void mboot_port_log_write(const char *s, size_t n) {
    (void)s;
    (void)n;
}

#endif
