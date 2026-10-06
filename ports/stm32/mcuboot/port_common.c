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

// The part of the mcuboot_port.h implementation for STM32H5 and STM32F7 that is the same in the
// bootloader and the application: handoff words, reset and, on STM32H5, the flash ECC event
// counter.

#include <stddef.h>

#include "port_stm32.h"
#include "mcuboot_config/mcuboot_config.h"
#include "mcuboot_request.h"

void mcuboot_stm32_backup_access(void) {
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

uint32_t mcuboot_port_retention_read(void) {
    mcuboot_stm32_backup_access();
    return MCUBOOT_STM32_BKP_RETENTION;
}

void mcuboot_port_retention_write(uint32_t v) {
    mcuboot_stm32_backup_access();
    MCUBOOT_STM32_BKP_RETENTION = v;
    (void)MCUBOOT_STM32_BKP_RETENTION;
}

void mcuboot_stm32_reset_flags_stash(uint32_t rsr) {
    mcuboot_stm32_backup_access();
    bool software_only = (rsr & MCUBOOT_STM32_RSTF_SOFT) && !(rsr & (MCUBOOT_STM32_RSTF_POR | MCUBOOT_STM32_RSTF_WDT | MCUBOOT_STM32_RSTF_LPWR));
    bool kept = (MCUBOOT_STM32_BKP_RESET_FLAGS & MCUBOOT_STM32_RESET_FLAGS_KEY_MASK) == MCUBOOT_STM32_RESET_FLAGS_KEY;
    if (software_only && kept) {
        return;
    }
    MCUBOOT_STM32_BKP_RESET_FLAGS = MCUBOOT_STM32_RESET_FLAGS_KEY | ((rsr >> 24) & 0xFFu);
    (void)MCUBOOT_STM32_BKP_RESET_FLAGS;
}

uint32_t mcuboot_stm32_reset_flags_take(uint32_t current) {
    mcuboot_stm32_backup_access();
    uint32_t v = MCUBOOT_STM32_BKP_RESET_FLAGS;
    if ((v & MCUBOOT_STM32_RESET_FLAGS_KEY_MASK) != MCUBOOT_STM32_RESET_FLAGS_KEY) {
        return current;
    }
    MCUBOOT_STM32_BKP_RESET_FLAGS = 0;
    return (v & 0xFFu) << 24;
}

void *mcuboot_port_request_ram(size_t *size_out) {
    if (size_out != NULL) {
        *size_out = MCUBOOT_REQ_REGION_SIZE;
    }
    return (void *)MCUBOOT_REQ_START;
}

MCUBOOT_NORETURN void mcuboot_port_reset(void) {
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
void mcuboot_port_wdt_feed(void) {
}

#if MCUBOOT_STM32_FLASH_ECC

// ---- flash ECC ----
//
// A read of a flash word with an invalid ECC returns the data and raises the NMI with
// FLASH_ECCDETR.ECCD set. The NMI handlers of the bootloader (port_core.c) and of the
// application (stm32_it.c) call mcuboot_stm32_nmi_ecc() and return.

volatile uint32_t mcuboot_ecc_nmi_count;
volatile uint32_t mcuboot_ecc_nmi_last;

bool mcuboot_stm32_nmi_ecc(void) {
    uint32_t detr = FLASH->ECCDETR;
    if (!(detr & FLASH_ECCR_ECCD)) {
        return false;
    }
    mcuboot_ecc_nmi_last = detr;
    mcuboot_ecc_nmi_count++;
    // Writing 1 clears the flag.
    FLASH->ECCDETR = FLASH_ECCR_ECCD;
    return true;
}

// A word left partly programmed by a power cut can be corrected on read instead of reported as
// a double error. FLASH_ECCCORR.ECCC is set (no NMI, the correction interrupt stays disabled)
// and the data is the erased value or a partial program. The flag is sticky, so each look that
// finds it set counts one correction and clears it.
static volatile uint32_t mcuboot_ecc_corrected_count;

uint32_t mcuboot_port_ecc_corrected(void) {
    if (FLASH->ECCCORR & FLASH_ECCR_ECCC) {
        mcuboot_ecc_corrected_count++;
        FLASH->ECCCORR = FLASH_ECCR_ECCC;
    }
    return mcuboot_ecc_corrected_count;
}

// A flag the NMI handler has not consumed yet counts as an event. That happens when the NMI is
// still pending or the caller runs in an NMI or fault handler.
uint32_t mcuboot_port_ecc_events(void) {
    mcuboot_stm32_nmi_ecc();
    return mcuboot_ecc_nmi_count;
}

void mcuboot_port_ecc_clear(void) {
    mcuboot_stm32_nmi_ecc();
}

#endif

#if defined(MCUBOOT_ROLE_APP)

// The application has no log output of its own.
void mcuboot_port_log_write(const char *s, size_t n) {
    (void)s;
    (void)n;
}

#endif
