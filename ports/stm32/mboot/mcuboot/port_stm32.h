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
#ifndef MICROPY_INCLUDED_STM32_MBOOT_PORT_STM32_H
#define MICROPY_INCLUDED_STM32_MBOOT_PORT_STM32_H

// Declarations shared by the files of the MCUboot port for STM32H5 and STM32F7. port_common.c and
// port_flash.c are built into both the bootloader and the application, the other files into the
// bootloader only.

#include <errno.h>
#include <stdbool.h>
#include <stdint.h>

#if __has_include("stm32h5xx.h")
#include "stm32h5xx.h"
#elif __has_include("stm32f7xx.h")
#include "stm32f7xx.h"
#else
#error "the MCUboot port in ports/stm32/mboot/mcuboot supports STM32H5 and STM32F7 only"
#endif
#include "mcuboot_config/mcuboot_config.h"
#include "mboot_port.h"

// Backup registers used by the port. They are not reset by a system reset. The application uses
// BKP19R (STM32H5) or BKP31R (STM32F7) for MICROPY_HW_CLK_LAST_FREQ only.
//   RETENTION: the 0x70AD00xx handoff word of mboot_port_retention_read/write().
//   FAULTS: 0x4D460000 | n, the number of consecutive bootloader fault resets (port_core.c).
//   RESET_FLAGS: 0x52530000 | (reset flags >> 24), the reset flags of the reset that started the
//     bootloader, for the application (the bootloader clears them, see port_common.c).
// The reset flags are those of RCC_RSR (STM32H5) or RCC_CSR (STM32F7), which have the same bits
// 24 to 31.
#if defined(STM32H5)
#define MBOOT_STM32_BKP_RETENTION (TAMP->BKP30R)
#define MBOOT_STM32_BKP_FAULTS (TAMP->BKP29R)
#define MBOOT_STM32_BKP_RESET_FLAGS (TAMP->BKP28R)
#define MBOOT_STM32_RCC_RESET_FLAGS (RCC->RSR)
#define MBOOT_STM32_RESET_FLAGS_CLEAR() (RCC->RSR |= RCC_RSR_RMVF)
#define MBOOT_STM32_RSTF_POR (RCC_RSR_BORRSTF)
#define MBOOT_STM32_RSTF_SOFT (RCC_RSR_SFTRSTF)
#define MBOOT_STM32_RSTF_WDT (RCC_RSR_IWDGRSTF | RCC_RSR_WWDGRSTF)
#define MBOOT_STM32_RSTF_LPWR (RCC_RSR_LPWRRSTF)
#define MBOOT_STM32_RSTF_PIN (RCC_RSR_PINRSTF)
// Flash words carry an ECC, see port_flash.c.
#define MBOOT_STM32_FLASH_ECC (1)
#else
#define MBOOT_STM32_BKP_RETENTION (RTC->BKP30R)
#define MBOOT_STM32_BKP_FAULTS (RTC->BKP29R)
#define MBOOT_STM32_BKP_RESET_FLAGS (RTC->BKP28R)
#define MBOOT_STM32_RCC_RESET_FLAGS (RCC->CSR)
#define MBOOT_STM32_RESET_FLAGS_CLEAR() (RCC->CSR |= RCC_CSR_RMVF)
#define MBOOT_STM32_RSTF_POR (RCC_CSR_PORRSTF | RCC_CSR_BORRSTF)
#define MBOOT_STM32_RSTF_SOFT (RCC_CSR_SFTRSTF)
#define MBOOT_STM32_RSTF_WDT (RCC_CSR_IWDGRSTF | RCC_CSR_WWDGRSTF)
#define MBOOT_STM32_RSTF_LPWR (RCC_CSR_LPWRRSTF)
#define MBOOT_STM32_RSTF_PIN (RCC_CSR_PINRSTF)
// Flash words have no ECC, see port_flash.c.
#define MBOOT_STM32_FLASH_ECC (0)
#endif
#define MBOOT_STM32_RESET_FLAGS_KEY 0x52530000u
#define MBOOT_STM32_RESET_FLAGS_KEY_MASK 0xFFFF0000u
#define MBOOT_STM32_FAULTS_KEY 0x4D460000u
#define MBOOT_STM32_FAULTS_KEY_MASK 0xFFFF0000u

// Enables the backup register clock and backup domain write access (TAMP on STM32H5, the RTC
// backup registers behind PWR_CR1.DBP on STM32F7).
void mboot_stm32_backup_access(void);

// Reset flags for the application. The bootloader clears the reset flags after reading them (as
// mboot_port_reset_cause() requires), so it first keeps the flags in a backup register. Flags
// are in the bit positions of RCC_RSR or RCC_CSR, and only bits 24 to 31 are kept.
// stash(): bootloader. Keeps rsr unless the flags of a software reset would replace those of an
//   earlier reset that no application has taken yet (the bootloader resets itself after a DFU
//   session or an fsload).
// take(): application, once at start. Returns the stashed flags and consumes them, or current
//   when there are none.
void mboot_stm32_reset_flags_stash(uint32_t rsr);
uint32_t mboot_stm32_reset_flags_take(uint32_t current);

#if MBOOT_STM32_FLASH_ECC
// Flash double ECC errors. The NMI handler increments the count and stores the FLASH_ECCDETR
// value of the last event.
extern volatile uint32_t mboot_ecc_nmi_count;
extern volatile uint32_t mboot_ecc_nmi_last;

// Counts and clears a pending FLASH_ECCDETR.ECCD flag. Returns whether there was one. The NMI
// handlers of the bootloader and the application call it.
bool mboot_stm32_nmi_ecc(void);
#endif

// Bootloader only (port_core.c): unique ID words cached before the caches are enabled (STM32H5
// faults on a UID read with the instruction cache enabled), a helper that puts a pin into an
// alternate function at very high speed, and the HCLK frequency.
extern uint32_t mboot_stm32_uid[3];
void mboot_stm32_gpio_af(GPIO_TypeDef *port, uint32_t pin, uint32_t af);
uint32_t mboot_stm32_hclk_hz(void);

// Controller primitives behind mboot_port_flash_dev_write/erase (port_flash.c). off is the byte
// offset in the flash (the start of a sector, or a multiple of the flash word). They return 0 or
// a negative errno and leave the caches without stale flash content. The fault-injection builds
// of the hardware tests (MBOOT_TEST_FI, STM32H5 only) execute them from RAM, with
// tests/mboot/hw/fi_flash.c.
#if defined(MBOOT_TEST_FI)
#define MBOOT_STM32_FLASH_PRIM __attribute__((long_call))
void mboot_stm32_fi_init(void);
#else
#define MBOOT_STM32_FLASH_PRIM
#endif
int mboot_stm32_flash_erase_sector(uint32_t off) MBOOT_STM32_FLASH_PRIM;
int mboot_stm32_flash_program_unit(uint32_t off, const uint32_t *w) MBOOT_STM32_FLASH_PRIM;

// Bounded busy wait: true when cond became true within loops evaluations. Nothing in the
// bootloader may wait forever for hardware, because recovery has to stay reachable.
#define MBOOT_STM32_WAIT_UNTIL(cond, loops) \
    ({ \
        bool ok_ = false; \
        for (uint32_t n_ = (loops); n_ != 0; n_--) { \
            if (cond) { \
                ok_ = true; \
                break; \
            } \
        } \
        ok_; \
    })

// Polls of ICACHE_SR.BSYENDF after an invalidation. The invalidation takes a few hundred
// cycles; the bound only has to end the wait if the cache never completes it.
#define MBOOT_STM32_ICACHE_WAIT_LOOPS (100000u)

#if defined(STM32H5)

// Invalidates the instruction cache if it is enabled. The cache sits on the C-AHB and holds
// flash data reads as well as instruction fetches, so invalidate it after every erase or program
// and before a read that must see the flash content (an ECC-invalid word is reported when its
// line is fetched from flash; a cached copy would hide it). Returns 0, or -ETIMEDOUT if the
// invalidation did not complete. The cache may then still return stale data, so the caller must
// fail the operation.
static inline __attribute__((always_inline)) int mboot_stm32_icache_invalidate(void) {
    if (!(ICACHE->CR & ICACHE_CR_EN)) {
        return 0;
    }
    ICACHE->FCR = ICACHE_FCR_CBSYENDF;
    ICACHE->CR |= ICACHE_CR_CACHEINV;
    bool done = MBOOT_STM32_WAIT_UNTIL(ICACHE->SR & ICACHE_SR_BSYENDF, MBOOT_STM32_ICACHE_WAIT_LOOPS);
    ICACHE->FCR = ICACHE_FCR_CBSYENDF;
    return done ? 0 : -ETIMEDOUT;
}

// Before a read of flash that has to report ECC errors, and after every erase or program of
// [addr, addr + len) of the flash.
static inline __attribute__((always_inline)) int mboot_stm32_flash_cache_prepare(void) {
    return mboot_stm32_icache_invalidate();
}

static inline __attribute__((always_inline)) int mboot_stm32_flash_cache_changed(uint32_t addr, uint32_t len) {
    (void)addr;
    (void)len;
    return mboot_stm32_icache_invalidate();
}

#else

// The flash is read through the data and instruction caches of the Cortex-M7 at 0x08000000, and
// the flash controller does not update them. After an erase or program the lines that cover the
// changed flash are invalidated (they are never dirty: the flash cannot be written by the CPU)
// and the instruction cache is dropped, so that a following read or fetch sees the new content.
// There is nothing to prepare before a read, flash words have no ECC.
static inline __attribute__((always_inline)) int mboot_stm32_flash_cache_prepare(void) {
    return 0;
}

static inline __attribute__((always_inline)) int mboot_stm32_flash_cache_changed(uint32_t addr, uint32_t len) {
    SCB_InvalidateDCache_by_Addr((volatile void *)addr, (int32_t)len);
    SCB_InvalidateICache();
    return 0;
}

#endif

#endif // MICROPY_INCLUDED_STM32_MBOOT_PORT_STM32_H
