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

// The policy for a memory mapped internal flash (device 0 of the flash map), the same in the
// bootloader and the application: bounds checks, reads that report an invalid ECC, programming
// that never disturbs a unit holding data, read-back verification and blank checks. It sits on the
// controller primitives of the port, erasing one sector and programming one unit, which the port
// takes from its flash driver.
//
// Properties of an ECC flash that the policy is built around (measured on an STM32H5):
// - Programming a word that does not read erased gives the bitwise AND of old and new data
//   with an invalid ECC. Programming a word of all 0xFF changes nothing. The policy therefore
//   skips all-0xFF units and refuses to program over a unit that holds other data.
// - Reading a word with an invalid ECC returns the data and raises the NMI, which ecc.c counts.
//   Reading erased flash does not fault, reading past the end of the flash is a bus fault.
// - A word whose program was cut short by a reset right at the start can instead read
//   "corrected": no NMI, and the data is the erased value or a partial program (0xEB for a flag
//   of 0x01 was observed). Such a word is not blank, so it is never programmed over;
//   mboot_port_ecc_corrected() tells the policy about it.
// - Fetches from the bank being erased or programmed stall until the operation is done, the
//   other bank keeps running.
// - An erase keeps running across a reset. The flash driver waits for it before it starts.
//
// Without ECC a unit is one 32-bit word. A unit that reads erased is programmed, a unit that holds
// other data is never programmed over, as with ECC. The glue relies on the program of one word being
// atomic across a power cut: a word that is not atomic can read as neither erased nor the new
// value. Such a word is refused by the policy (it is not blank), a torn counter record is
// rejected by its CRC and a torn log record is skipped. A torn word in the body of an image fails
// the image validation and the device ends in DFU. Nothing here shadows a word.

#include <errno.h>
#include <string.h>

#include "mboot_layout.h"
#include "mboot_port.h"

#define FLASH_BASE_ADDR (MICROPY_HW_MBOOT_DEV0_BASE)
#define FLASH_TOTAL_SIZE (MICROPY_HW_MBOOT_DEV0_SIZE)
#define FLASH_UNIT_BYTES (MICROPY_HW_MBOOT_DEV0_WRITE)
#define FLASH_UNIT_WORDS (FLASH_UNIT_BYTES / 4)

#if defined(__arm__)
#define BARRIER() __asm volatile ("dsb\n isb" ::: "memory")
#else
#define BARRIER() __asm volatile ("" ::: "memory")
#endif

#if MICROPY_HW_MBOOT_DEV0_ECC
// ECC events of the reads and programs of a unit. The mark macros declare the variables they use.
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
#ifndef MICROPY_HW_MBOOT_FLASH_VERIFY
#define MICROPY_HW_MBOOT_FLASH_VERIFY (1)
#endif

static int check_range(uint32_t off, uint32_t len) {
    if (off > FLASH_TOTAL_SIZE || len > FLASH_TOTAL_SIZE - off) {
        return -EINVAL;
    }
    return 0;
}

int mboot_flash_internal_read(uint32_t off, void *dst, uint32_t len) {
    int rc = check_range(off, len);
    if (rc != 0) {
        return rc;
    }
    if (len == 0) {
        return 0;
    }
    // A word fetched into the cache before it went bad would be returned without a new ECC
    // event, so start the read with an empty cache.
    rc = mboot_port_flash_cache_prepare();
    if (rc != 0) {
        return rc;
    }
    ECC_EVENTS_BEGIN();
    memcpy(dst, (const void *)(FLASH_BASE_ADDR + off), len);
    BARRIER();
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
    volatile const uint32_t *cur = (volatile const uint32_t *)(FLASH_BASE_ADDR + off);
    ECC_FULL_BEGIN();
    uint32_t c[FLASH_UNIT_WORDS];
    for (unsigned i = 0; i < FLASH_UNIT_WORDS; i++) {
        c[i] = cur[i];
    }
    BARRIER();
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

int mboot_flash_internal_write(uint32_t off, const void *src, uint32_t len) {
    int rc = check_range(off, len);
    if (rc != 0) {
        return rc;
    }
    if (((off | len) & (FLASH_UNIT_BYTES - 1)) != 0) {
        return -EINVAL;
    }
    const uint8_t *s = (const uint8_t *)src;

    // Nothing is programmed unless every unit can be.
    rc = mboot_port_flash_cache_prepare();
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
        rc = mboot_port_flash_program_unit(off + pos, w);
        if (rc != 0) {
            return rc;
        }
        #if MICROPY_HW_MBOOT_FLASH_VERIFY
        volatile const uint32_t *cur = (volatile const uint32_t *)(FLASH_BASE_ADDR + off + pos);
        ECC_FULL_BEGIN();
        bool same = true;
        for (unsigned i = 0; i < FLASH_UNIT_WORDS; i++) {
            same = same && cur[i] == w[i];
        }
        BARRIER();
        if (!same || ECC_FULL_SEEN()) {
            return -EIO;
        }
        #endif
    }
    return 0;
}

int mboot_flash_internal_erase(uint32_t off, uint32_t len) {
    int rc = check_range(off, len);
    if (rc != 0) {
        return rc;
    }
    // The range is whole sectors, which may differ in size from one run to the next. Nothing is
    // erased unless it is.
    const mboot_flash_dev_t *d = &mboot_devs[0];
    if (!mboot_dev_erase_range_ok(d, off, len)) {
        return -EINVAL;
    }
    for (uint32_t pos = off; pos < off + len;) {
        uint32_t size = mboot_dev_erase_at(d, pos);
        rc = mboot_port_flash_erase_sector(pos);
        if (rc != 0) {
            return rc;
        }
        // The sector must read back erased without an ECC event.
        volatile const uint32_t *p = (volatile const uint32_t *)(FLASH_BASE_ADDR + pos);
        ECC_EVENTS_BEGIN();
        uint32_t acc = 0xFFFFFFFFu;
        for (uint32_t i = 0; i < size / sizeof(uint32_t); i++) {
            acc &= p[i];
        }
        BARRIER();
        if (acc != 0xFFFFFFFFu || ECC_EVENTS_SEEN()) {
            return -EIO;
        }
        pos += size;
    }
    return 0;
}
