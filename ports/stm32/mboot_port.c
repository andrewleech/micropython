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

// The part of the mboot_port.h implementation for the stm32 port that is the same in the
// bootloader (mboot_boot.c has the rest) and the application: the backup words, reset, the flash
// ECC flags and the flash controller primitives under shared/mboot/src/flash_internal.c. It is
// built on the HAL and the flash driver (flash.c) of the port.

#include <errno.h>

#include "py/mphal.h"
#include "irq.h"
#include "flash.h"
#include "mboot_layout.h"
#include "mboot_request.h"
#include "mboot_port.h"

// A SPI flash as device 1, configured like the one of ports/stm32/mboot.
#if defined(MICROPY_HW_MBOOT_SPIFLASH_ADDR)
#include "storage.h"
#include "mboot_spiflash.h"
#endif

// ---- backup words ----
//
// The words of the TAMP or RTC backup registers are not reset by a system reset. The application
// uses one of them for MICROPY_HW_CLK_LAST_FREQ (BKP19R, BKP31R on STM32F7).

#if defined(TAMP)
#define BKP_UNIT TAMP
#else
#define BKP_UNIT RTC
#endif

static volatile uint32_t *const s_bkp[MBOOT_BKP_COUNT] = {
    [MBOOT_BKP_RETENTION] = &BKP_UNIT->BKP30R,
    [MBOOT_BKP_FAULTS] = &BKP_UNIT->BKP29R,
    [MBOOT_BKP_RESET_FLAGS] = &BKP_UNIT->BKP28R,
    [MBOOT_BKP_RESET_FLAGS_CHECK] = &BKP_UNIT->BKP27R,
};

// Enables the backup register clock and the backup domain write access.
static void backup_access(void) {
    #if defined(RCC_APB3ENR_RTCAPBEN)
    __HAL_RCC_RTCAPB_CLK_ENABLE();
    #else
    __HAL_RCC_PWR_CLK_ENABLE();
    #endif
    HAL_PWR_EnableBkUpAccess();
}

uint32_t mboot_port_bkp_read(unsigned idx) {
    backup_access();
    return *s_bkp[idx];
}

void mboot_port_bkp_write(unsigned idx, uint32_t v) {
    backup_access();
    *s_bkp[idx] = v;
    (void)*s_bkp[idx];
}

void *mboot_port_request_ram(size_t *size_out) {
    if (size_out != NULL) {
        *size_out = MBOOT_REQ_REGION_SIZE;
    }
    return (void *)MBOOT_REQ_START;
}

MBOOT_NORETURN void mboot_port_reset(void) {
    #if __DCACHE_PRESENT == 1
    // The data cache holds the request region (and everything else the application wrote last)
    // until it is cleaned. The reset discards the cache, so write it back first.
    SCB_CleanDCache();
    #endif
    __DSB();
    NVIC_SystemReset();
}

// No watchdog is started by the bootloader or by this glue.
void mboot_port_wdt_feed(void) {
}

#if defined(MBOOT_ROLE_APP)

// The application has no log output of its own.
void mboot_port_log_write(const char *s, size_t n) {
    (void)s;
    (void)n;
}

#endif

// ---- flash ECC ----

#if FLASH_WORD_HAS_ECC

bool mboot_port_ecc_detect_take(uint32_t *info) {
    uint32_t detr = FLASH->ECCDETR;
    if (!(detr & FLASH_ECCR_ECCD)) {
        return false;
    }
    *info = detr;
    // Writing 1 clears the flag.
    FLASH->ECCDETR = FLASH_ECCR_ECCD;
    return true;
}

bool mboot_port_ecc_corrected_take(void) {
    if (!(FLASH->ECCCORR & FLASH_ECCR_ECCC)) {
        return false;
    }
    FLASH->ECCCORR = FLASH_ECCR_ECCC;
    return true;
}

#endif

// ---- flash ----

// The flash is read through the caches, which the flash controller does not update. The ST
// instruction cache of the STM32H5 sits on the C-AHB and holds flash data reads as well as
// instruction fetches: it is invalidated after every erase or program, and before a read that has
// to report an ECC error (an invalid word is reported when its line is fetched from flash, a
// cached copy would hide it). The data and instruction caches of a Cortex-M7 hold flash lines that
// are never dirty (the CPU cannot write the flash), so the lines that cover changed flash are
// invalidated. Both return 0, or a negative errno if the invalidation does not complete: the
// cache may then still return stale data, so the caller fails the operation.

#if defined(ICACHE)

static int icache_invalidate(void) {
    if (!HAL_ICACHE_IsEnabled()) {
        return 0;
    }
    return HAL_ICACHE_Invalidate() == HAL_OK ? 0 : -ETIMEDOUT;
}

int mboot_port_flash_cache_prepare(void) {
    return icache_invalidate();
}

static int flash_cache_changed(uint32_t addr, uint32_t len) {
    (void)addr;
    (void)len;
    return icache_invalidate();
}

#else

int mboot_port_flash_cache_prepare(void) {
    return 0;
}

static int flash_cache_changed(uint32_t addr, uint32_t len) {
    #if __DCACHE_PRESENT == 1
    SCB_InvalidateDCache_by_Addr((volatile void *)addr, (int32_t)len);
    #endif
    #if __ICACHE_PRESENT == 1
    SCB_InvalidateICache();
    #endif
    (void)addr;
    (void)len;
    return 0;
}

#endif

// The flash driver runs with the interrupts that flush the filesystem cache to flash
// (flashbdev.c) held off, like the filesystem code does.

int mboot_port_flash_erase_sector(uint32_t off) {
    uint32_t addr = MICROPY_HW_MBOOT_DEV0_BASE + off;
    uint32_t basepri = raise_irq_pri(IRQ_PRI_FLASH);
    #if defined(ICACHE) && defined(MBOOT_ROLE_BOOTLOADER)
    // flash_erase() reads the size of the flash from the system flash area, which faults while
    // the bootloader has the instruction cache enabled.
    bool icache_on = HAL_ICACHE_IsEnabled();
    if (icache_on) {
        HAL_ICACHE_Disable();
    }
    #endif
    int rc = flash_erase(addr);
    #if defined(ICACHE) && defined(MBOOT_ROLE_BOOTLOADER)
    if (icache_on) {
        HAL_ICACHE_Enable();
    }
    #endif
    restore_irq_pri(basepri);
    int ic = flash_cache_changed(addr, mboot_dev_erase_at(&mboot_devs[0], off));
    return rc != 0 ? rc : ic;
}

int mboot_port_flash_program_unit(uint32_t off, const uint32_t *w) {
    uint32_t addr = MICROPY_HW_MBOOT_DEV0_BASE + off;
    uint32_t basepri = raise_irq_pri(IRQ_PRI_FLASH);
    int rc = flash_write(addr, w, MICROPY_HW_MBOOT_DEV0_WRITE / 4);
    restore_irq_pri(basepri);
    int ic = flash_cache_changed(addr, MICROPY_HW_MBOOT_DEV0_WRITE);
    return rc != 0 ? rc : ic;
}

#if defined(MBOOT_ROLE_BOOTLOADER)

int mboot_port_flash_dev_init(void) {
    #if defined(MICROPY_HW_MBOOT_SPIFLASH_ADDR)
    #if defined(MICROPY_BOARD_EARLY_INIT)
    // The board sets the parameters of the SPI flash chips it can carry before the first use.
    MICROPY_BOARD_EARLY_INIT();
    #endif
    MICROPY_HW_MBOOT_SPIFLASH_SPIFLASH->config = MICROPY_HW_MBOOT_SPIFLASH_CONFIG;
    mp_spiflash_init(MICROPY_HW_MBOOT_SPIFLASH_SPIFLASH);
    #endif
    return 0;
}

#endif

int mboot_port_flash_dev_read(uint8_t dev, uint32_t off, void *dst, uint32_t len) {
    #if defined(MICROPY_HW_MBOOT_SPIFLASH_ADDR)
    if (dev == 1) {
        return mboot_spiflash_read(MICROPY_HW_MBOOT_SPIFLASH_SPIFLASH, off, dst, len);
    }
    #endif
    return dev == 0 ? mboot_flash_internal_read(off, dst, len) : -ENODEV;
}

int mboot_port_flash_dev_write(uint8_t dev, uint32_t off, const void *src, uint32_t len) {
    #if defined(MICROPY_HW_MBOOT_SPIFLASH_ADDR)
    if (dev == 1) {
        return mboot_spiflash_write(MICROPY_HW_MBOOT_SPIFLASH_SPIFLASH, off, src, len);
    }
    #endif
    return dev == 0 ? mboot_flash_internal_write(off, src, len) : -ENODEV;
}

int mboot_port_flash_dev_erase(uint8_t dev, uint32_t off, uint32_t len) {
    #if defined(MICROPY_HW_MBOOT_SPIFLASH_ADDR)
    if (dev == 1) {
        return mboot_spiflash_erase(MICROPY_HW_MBOOT_SPIFLASH_SPIFLASH, off, len);
    }
    #endif
    return dev == 0 ? mboot_flash_internal_erase(off, len) : -ENODEV;
}
