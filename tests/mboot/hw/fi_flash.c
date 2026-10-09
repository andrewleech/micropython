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

// Register level flash driver of the fault-injection builds of the bootloader (MBOOT_TEST_FI,
// used by the hardware campaigns in this directory). It replaces the two controller primitives
// of ports/stm32/mboot/mcuboot/port_flash.c with versions that execute from RAM, so the CPU is not held
// while the flash works and a software reset can land inside the program pulse of a flash word,
// and it implements the test hook that triggers those resets.
//
// The mode word of the fault-injection state (shared/mboot/include/mboot_request.h) holds the
// mode in bits 0-1 (0 reset before the operation, 1 after it, 2 during it) and the delay in
// microseconds for mode 2 in bits 8-31 (0 selects FI_DEFAULT_TEAR_US).
//
// The driver accesses the controller through its non-secure registers (TZEN=0).

#include <errno.h>

#include "mboot_request.h"
#include "port_stm32.h"

#define FLASH_BANK_BYTES (MBOOT_DEV0_SIZE / 2)

#define FLASH_KEY_1 (0x45670123u)
#define FLASH_KEY_2 (0xCDEF89ABu)

#define FLASH_SR_ERRORS \
    (FLASH_SR_WRPERR | FLASH_SR_PGSERR | FLASH_SR_STRBERR | FLASH_SR_INCERR \
    | FLASH_SR_OBKERR | FLASH_SR_OBKWERR | FLASH_SR_OPTCHANGEERR)

// Upper bound of the status polling loops. The longest operation, a sector erase, takes a few
// milliseconds.
#define FLASH_BUSY_LOOPS (0x4000000u)

#define RAMFUNC __attribute__((section(".ramfunc"), noinline))
#define RAMFUNC_INLINE static inline __attribute__((always_inline))

#define FI_MAGIC (0x4A4E4946u)
// Delay inside the window in which a reset leaves an ECC-invalid word: measured on the NUCLEO-H563ZI
// (tear_probe.py) as 10 to 14 microseconds after the command start, with the word erased below 10
// and fully programmed from 15.
#define FI_DEFAULT_TEAR_US (12u)

// Cycles to wait after starting the next flash command before resetting (mode 2). Armed by
// mboot_port_fi_hook() and used by the next command.
static uint32_t s_tear_cycles;

RAMFUNC_INLINE void tear_point(uint32_t cycles) {
    if (cycles != 0) {
        uint32_t t0 = DWT->CYCCNT;
        while ((uint32_t)(DWT->CYCCNT - t0) < cycles) {
        }
        SCB->AIRCR = (0x5FAu << SCB_AIRCR_VECTKEY_Pos) | SCB_AIRCR_SYSRESETREQ_Msk;
        for (;;) {
        }
    }
}

static uint32_t take_tear_cycles(void) {
    uint32_t c = s_tear_cycles;
    s_tear_cycles = 0;
    return c;
}

RAMFUNC_INLINE void flash_unlock(void) {
    if (FLASH->NSCR & FLASH_CR_LOCK) {
        FLASH->NSKEYR = FLASH_KEY_1;
        FLASH->NSKEYR = FLASH_KEY_2;
    }
}

RAMFUNC_INLINE void flash_lock(void) {
    FLASH->NSCR |= FLASH_CR_LOCK;
}

// Waits until the controller is idle, then clears the flags of the previous command. The flags of
// the new command are collected by flash_finish().
RAMFUNC_INLINE int flash_begin(void) {
    for (uint32_t n = FLASH_BUSY_LOOPS; FLASH->NSSR & (FLASH_SR_BSY | FLASH_SR_DBNE); n--) {
        if (n == 0) {
            return -ETIMEDOUT;
        }
    }
    FLASH->NSCCR = FLASH->NSSR & (FLASH_SR_EOP | FLASH_SR_ERRORS);
    return 0;
}

RAMFUNC_INLINE int flash_finish(void) {
    for (uint32_t n = FLASH_BUSY_LOOPS; FLASH->NSSR & (FLASH_SR_BSY | FLASH_SR_DBNE); n--) {
        if (n == 0) {
            return -ETIMEDOUT;
        }
    }
    uint32_t sr = FLASH->NSSR;
    FLASH->NSCCR = sr & (FLASH_SR_EOP | FLASH_SR_ERRORS);
    return (sr & FLASH_SR_ERRORS) ? -EIO : 0;
}

RAMFUNC int mboot_stm32_flash_erase_sector(uint32_t off) {
    uint32_t tear_cycles = take_tear_cycles();
    // With FLASH_OPTCR.SWAP_BANK set the lower half of the address range is bank 2.
    uint32_t bank2 = (off >= FLASH_BANK_BYTES) ^ ((FLASH->OPTCR >> FLASH_OPTCR_SWAP_BANK_Pos) & 1u);
    uint32_t snb = (off & (FLASH_BANK_BYTES - 1)) / FLASH_SECTOR_SIZE;

    flash_unlock();
    int rc = flash_begin();
    if (rc == 0) {
        FLASH->NSCR = FLASH_CR_SER | (snb << FLASH_CR_SNB_Pos) | (bank2 ? FLASH_CR_BKSEL : 0);
        FLASH->NSCR |= FLASH_CR_START;
        tear_point(tear_cycles);
        rc = flash_finish();
        FLASH->NSCR &= ~(FLASH_CR_SER | FLASH_CR_BKSEL | FLASH_CR_SNB);
    }
    flash_lock();
    int ic = mboot_stm32_icache_invalidate();
    return rc != 0 ? rc : ic;
}

// The data is loaded into registers before the controller is armed so that nothing is read from
// the flash while the write buffer is partly filled.
RAMFUNC int mboot_stm32_flash_program_unit(uint32_t off, const uint32_t *w) {
    uint32_t tear_cycles = take_tear_cycles();
    uint32_t w0 = w[0];
    uint32_t w1 = w[1];
    uint32_t w2 = w[2];
    uint32_t w3 = w[3];
    volatile uint32_t *dst = (volatile uint32_t *)(FLASH_BASE + off);

    flash_unlock();
    int rc = flash_begin();
    if (rc == 0) {
        // The controller programs when the fourth word arrives, so an interrupt must not break
        // the sequence.
        uint32_t primask = __get_PRIMASK();
        __disable_irq();
        FLASH->NSCR |= FLASH_CR_PG;
        dst[0] = w0;
        dst[1] = w1;
        dst[2] = w2;
        dst[3] = w3;
        __ISB();
        __DSB();
        __set_PRIMASK(primask);
        tear_point(tear_cycles);
        rc = flash_finish();
        FLASH->NSCR &= ~FLASH_CR_PG;
    }
    flash_lock();
    int ic = mboot_stm32_icache_invalidate();
    return rc != 0 ? rc : ic;
}

void mboot_stm32_fi_init(void) {
    DCB->DEMCR |= DCB_DEMCR_TRCENA_Msk;
    DWT->CYCCNT = 0;
    DWT->CTRL |= DWT_CTRL_CYCCNTENA_Msk;

    // An erase that was running when the last reset happened is still running.
    flash_begin();
    FLASH->NSCR |= FLASH_CR_LOCK;
}

void mboot_port_fi_hook(int op, uint8_t dev, uint32_t off, uint32_t len, int phase) {
    (void)op;
    (void)dev;
    (void)off;
    (void)len;
    volatile uint32_t *st = (volatile uint32_t *)((uint8_t *)mboot_port_request_ram(NULL) + MBOOT_FI_STATE_OFFSET);
    if (st[0] != FI_MAGIC || st[1] == 0 || st[2] != st[1]) {
        return;
    }
    uint32_t mode = st[3] & 3u;
    if ((phase == 0 && mode == 0) || (phase == 1 && mode == 1)) {
        mboot_port_reset();
    }
    if (phase == 0 && mode == 2) {
        uint32_t us = st[3] >> 8;
        s_tear_cycles = (us != 0 ? us : FI_DEFAULT_TEAR_US) * (mboot_stm32_hclk_hz() / 1000000u);
    }
}
