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

// Internal flash of the STM32H5 or STM32F7 as device 0 of mboot_port_flash_*. The STM32H5 flash
// is two equal banks of 8 KiB sectors programmed in 128-bit flash words, each with its own ECC.
// The STM32F7 flash has sectors of 32, 128 and 256 KiB (runs of equal sectors, as in
// mboot_dev.h) programmed in 32-bit words without ECC. A SPI flash configured like the one of
// ports/stm32/mboot is device 1.
//
// This file holds the policy for the internal flash, the same in the bootloader and the
// application: bounds checks, reads that report an invalid ECC (STM32H5), programming that never
// disturbs a unit holding data, read-back verification and blank checks. It sits on two
// controller primitives, erasing one sector and programming one unit, which come from the port's
// flash driver (flash.c).
//
// Properties of the STM32H5 flash the policy is built around (measured on a NUCLEO-H563ZI):
// - Programming a word that does not read erased gives the bitwise AND of old and new data
//   with an invalid ECC. Programming a word of all 0xFF changes nothing. The policy therefore
//   skips all-0xFF units and refuses to program over a unit that holds other data.
// - Reading a word with an invalid ECC returns the data and raises the NMI with
//   FLASH_ECCDETR.ECCD set (counted in port_common.c). Reading erased flash does not fault,
//   reading past the end of the flash is a bus fault.
// - A word whose program was cut short by a reset right at the start can instead read
//   "corrected" (FLASH_ECCCORR.ECCC, counted as well): no NMI, and the data is the erased value
//   or a partial program (0xEB for a flag of 0x01 was observed). Such a word is not blank, so
//   it is never programmed over; mboot_port_ecc_corrected() tells the policy layer about it.
// - Fetches from the bank being erased or programmed stall until the operation is done, the
//   other bank keeps running.
// - An erase keeps running across a reset. The flash driver waits for it before it starts.
//
// Without ECC (STM32F7) a unit is one 32-bit word. A unit that reads erased is programmed, a
// unit that holds other data is never programmed over, as on the STM32H5. The glue relies on the
// program of one word being atomic across a power cut: a word that is not atomic can read as
// neither erased nor the new value. Such a word is refused by the policy (it is not blank). A
// torn counter record is rejected by its CRC. A torn word in the body of an image fails image
// validation and the device ends in DFU. Nothing here shadows a word.

#include <errno.h>
#include <string.h>

#include "py/mphal.h"
#include "irq.h"
#include "flash.h"
#include "mboot_request.h"
#include "port_stm32.h"

// A SPI flash as device 1, configured like the one of ports/stm32/mboot.
#if defined(MBOOT_SPIFLASH_ADDR)
#include "storage.h"
#include "mboot_spiflash.h"
#endif

// Device 0 of the flash map is the whole flash. The program unit (one flash word) is that of
// mboot_dev.h. The sectors are those of mboot_dev.h, uniform or in runs.
#define FLASH_TOTAL_SIZE (MBOOT_DEV0_SIZE)
#define FLASH_UNIT_BYTES (MBOOT_DEV0_WRITE)
#define FLASH_UNIT_WORDS (FLASH_UNIT_BYTES / 4)

#if MBOOT_STM32_FLASH_ECC
// ECC events of the reads and programs of a unit, see below. The mark macros declare the
// variables they use.
#define ECC_EVENTS_BEGIN() uint32_t ecc_events_ = mboot_port_ecc_events()
#define ECC_EVENTS_SEEN() (mboot_port_ecc_events() != ecc_events_)
#define ECC_FULL_BEGIN() \
    uint32_t ecc_events_ = mboot_port_ecc_events(); \
    uint32_t ecc_corrected_ = mboot_port_ecc_corrected()
#define ECC_FULL_SEEN() (mboot_port_ecc_events() != ecc_events_ || mboot_port_ecc_corrected() != ecc_corrected_)
#else
#define ECC_EVENTS_BEGIN()
#define ECC_EVENTS_SEEN() (false)
#define ECC_FULL_BEGIN()
#define ECC_FULL_SEEN() (false)
#endif

// Program verification: every programmed unit is read back and compared.
#ifndef MBOOT_STM32_FLASH_VERIFY
#define MBOOT_STM32_FLASH_VERIFY (1)
#endif

// Size of the sector that starts at byte offset off of the flash, 0 if none starts there.
static uint32_t sector_size_at(uint32_t off) {
    #if defined(MBOOT_DEV0_ERASE)
    return (off & (MBOOT_DEV0_ERASE - 1)) == 0 ? MBOOT_DEV0_ERASE : 0;
    #else
    uint32_t start;
    uint32_t size;
    if (flash_get_sector_info(MBOOT_DEV0_BASE + off, &start, &size) < 0 || start != MBOOT_DEV0_BASE + off) {
        return 0;
    }
    return size;
    #endif
}

// ---- controller primitives ----

// The fault-injection builds of the hardware tests (MBOOT_TEST_FI) use a register level driver
// that can reset inside the program pulse, tests/mboot/hw/fi_flash.c.
#if !defined(MBOOT_TEST_FI)

// The flash driver runs with the interrupts that flush the filesystem cache to flash
// (flashbdev.c) held off, like the filesystem code does.

// Erases the sector at byte offset off (the start of a sector) of the flash.
int mboot_stm32_flash_erase_sector(uint32_t off) {
    uint32_t basepri = raise_irq_pri(IRQ_PRI_FLASH);
    #if defined(STM32H5) && defined(MBOOT_ROLE_BOOTLOADER)
    // flash_erase() reads the size of the flash (FLASH_SIZE) from the system flash area, which
    // faults while the bootloader has the instruction cache enabled.
    ICACHE->CR &= ~ICACHE_CR_EN;
    #endif
    int rc = flash_erase(MBOOT_DEV0_BASE + off);
    #if defined(STM32H5) && defined(MBOOT_ROLE_BOOTLOADER)
    ICACHE->CR |= ICACHE_CR_EN;
    #endif
    restore_irq_pri(basepri);
    // The caches hold flash data as well as code, and the flash driver does not invalidate
    // them.
    int ic = mboot_stm32_flash_cache_changed(MBOOT_DEV0_BASE + off, sector_size_at(off));
    return rc != 0 ? rc : ic;
}

// Programs one flash unit at byte offset off from the words in w.
int mboot_stm32_flash_program_unit(uint32_t off, const uint32_t *w) {
    uint32_t basepri = raise_irq_pri(IRQ_PRI_FLASH);
    int rc = flash_write(MBOOT_DEV0_BASE + off, w, FLASH_UNIT_WORDS);
    restore_irq_pri(basepri);
    int ic = mboot_stm32_flash_cache_changed(MBOOT_DEV0_BASE + off, FLASH_UNIT_BYTES);
    return rc != 0 ? rc : ic;
}

#endif

#if defined(MBOOT_ROLE_BOOTLOADER)

int mboot_port_flash_dev_init(void) {
    #if defined(MBOOT_TEST_FI)
    mboot_stm32_fi_init();
    #endif

    #if defined(MBOOT_SPIFLASH_ADDR)
    #if defined(MICROPY_BOARD_EARLY_INIT)
    // The board sets the parameters of the SPI flash chips it can carry before the first use.
    MICROPY_BOARD_EARLY_INIT();
    #endif
    MBOOT_SPIFLASH_SPIFLASH->config = MBOOT_SPIFLASH_CONFIG;
    mp_spiflash_init(MBOOT_SPIFLASH_SPIFLASH);
    #endif
    return 0;
}

#endif

// ---- policy ----

static int check_range(uint8_t dev, uint32_t off, uint32_t len) {
    if (dev != 0) {
        return -ENODEV;
    }
    if (off > FLASH_TOTAL_SIZE || len > FLASH_TOTAL_SIZE - off) {
        return -EINVAL;
    }
    return 0;
}

int mboot_port_flash_dev_read(uint8_t dev, uint32_t off, void *dst, uint32_t len) {
    #if defined(MBOOT_SPIFLASH_ADDR)
    if (dev == 1) {
        return mboot_spiflash_read(MBOOT_SPIFLASH_SPIFLASH, off, dst, len);
    }
    #endif
    int rc = check_range(dev, off, len);
    if (rc != 0) {
        return rc;
    }
    if (len == 0) {
        return 0;
    }
    // A word fetched into the cache before it went bad would be returned without a new ECC
    // event, so start the read with an empty cache.
    rc = mboot_stm32_flash_cache_prepare();
    if (rc != 0) {
        return rc;
    }
    ECC_EVENTS_BEGIN();
    memcpy(dst, (const void *)(MBOOT_DEV0_BASE + off), len);
    __DSB();
    __ISB();
    return ECC_EVENTS_SEEN() ? -EIO : 0;
}

typedef enum {
    UNIT_SKIP,      // nothing to program: data is all 0xFF, or the unit already holds it
    UNIT_PROGRAM,   // erased, to be programmed
    UNIT_REFUSE,    // holds other data or has an invalid ECC
} unit_action_t;

static unit_action_t classify_unit(uint32_t off, const uint32_t *w) {
    uint32_t blank = 0xFFFFFFFFu;
    for (unsigned i = 0; i < FLASH_UNIT_WORDS; i++) {
        blank &= w[i];
    }
    if (blank == 0xFFFFFFFFu) {
        return UNIT_SKIP;
    }
    volatile const uint32_t *cur = (volatile const uint32_t *)(MBOOT_DEV0_BASE + off);
    ECC_FULL_BEGIN();
    uint32_t c[FLASH_UNIT_WORDS];
    for (unsigned i = 0; i < FLASH_UNIT_WORDS; i++) {
        c[i] = cur[i];
    }
    __DSB();
    __ISB();
    if (ECC_FULL_SEEN()) {
        return UNIT_REFUSE;
    }
    blank = 0xFFFFFFFFu;
    bool same = true;
    for (unsigned i = 0; i < FLASH_UNIT_WORDS; i++) {
        blank &= c[i];
        same = same && c[i] == w[i];
    }
    if (blank == 0xFFFFFFFFu) {
        return UNIT_PROGRAM;
    }
    return same ? UNIT_SKIP : UNIT_REFUSE;
}

int mboot_port_flash_dev_write(uint8_t dev, uint32_t off, const void *src, uint32_t len) {
    #if defined(MBOOT_SPIFLASH_ADDR)
    if (dev == 1) {
        return mboot_spiflash_write(MBOOT_SPIFLASH_SPIFLASH, off, src, len);
    }
    #endif
    int rc = check_range(dev, off, len);
    if (rc != 0) {
        return rc;
    }
    if (((off | len) & (FLASH_UNIT_BYTES - 1)) != 0) {
        return -EINVAL;
    }
    const uint8_t *s = (const uint8_t *)src;

    // Nothing is programmed unless every unit can be.
    rc = mboot_stm32_flash_cache_prepare();
    if (rc != 0) {
        return rc;
    }
    for (uint32_t pos = 0; pos < len; pos += FLASH_UNIT_BYTES) {
        uint32_t w[FLASH_UNIT_WORDS];
        memcpy(w, s + pos, FLASH_UNIT_BYTES);
        if (classify_unit(off + pos, w) == UNIT_REFUSE) {
            return -EIO;
        }
    }

    for (uint32_t pos = 0; pos < len; pos += FLASH_UNIT_BYTES) {
        uint32_t w[FLASH_UNIT_WORDS];
        memcpy(w, s + pos, FLASH_UNIT_BYTES);
        if (classify_unit(off + pos, w) != UNIT_PROGRAM) {
            continue;
        }
        rc = mboot_stm32_flash_program_unit(off + pos, w);
        if (rc != 0) {
            return rc;
        }
        #if MBOOT_STM32_FLASH_VERIFY
        volatile const uint32_t *cur = (volatile const uint32_t *)(MBOOT_DEV0_BASE + off + pos);
        ECC_FULL_BEGIN();
        bool same = true;
        for (unsigned i = 0; i < FLASH_UNIT_WORDS; i++) {
            same = same && cur[i] == w[i];
        }
        __DSB();
        __ISB();
        if (!same || ECC_FULL_SEEN()) {
            return -EIO;
        }
        #endif
    }
    return 0;
}

int mboot_port_flash_dev_erase(uint8_t dev, uint32_t off, uint32_t len) {
    #if defined(MBOOT_SPIFLASH_ADDR)
    if (dev == 1) {
        return mboot_spiflash_erase(MBOOT_SPIFLASH_SPIFLASH, off, len);
    }
    #endif
    int rc = check_range(dev, off, len);
    if (rc != 0) {
        return rc;
    }
    // The range is whole sectors, which may differ in size from one to the next. Nothing is
    // erased unless it is.
    #if defined(MBOOT_DEV0_ERASE)
    if (((off | len) & (MBOOT_DEV0_ERASE - 1)) != 0) {
        return -EINVAL;
    }
    #else
    for (uint32_t pos = off; pos < off + len;) {
        uint32_t size = sector_size_at(pos);
        if (size == 0 || size > off + len - pos) {
            return -EINVAL;
        }
        pos += size;
    }
    #endif
    for (uint32_t pos = off; pos < off + len;) {
        uint32_t size = sector_size_at(pos);
        rc = mboot_stm32_flash_erase_sector(pos);
        if (rc != 0) {
            return rc;
        }
        // The sector must read back erased without an ECC event.
        volatile const uint32_t *p = (volatile const uint32_t *)(MBOOT_DEV0_BASE + pos);
        ECC_EVENTS_BEGIN();
        uint32_t acc = 0xFFFFFFFFu;
        for (uint32_t i = 0; i < size / sizeof(uint32_t); i++) {
            acc &= p[i];
        }
        __DSB();
        __ISB();
        if (acc != 0xFFFFFFFFu || ECC_EVENTS_SEEN()) {
            return -EIO;
        }
        pos += size;
    }
    return 0;
}
