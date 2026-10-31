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

// The words a port keeps across resets (mboot_port_bkp_read/write) and what they hold: the
// handoff word of mboot_request.h and the reset flags of the reset that started the bootloader.
// Both roles.

#include "mboot_port.h"

uint32_t mboot_port_retention_read(void) {
    return mboot_port_bkp_read(MBOOT_BKP_RETENTION);
}

void mboot_port_retention_write(uint32_t v) {
    mboot_port_bkp_write(MBOOT_BKP_RETENTION, v);
}

// The reset flags are kept in two words, the flags and the flags XOR a key, so that the content of
// a backup domain that was never written (or lost its power) is not taken for a handoff.
#define RESET_FLAGS_KEY 0x52534631u

static bool reset_flags_kept(void) {
    return (mboot_port_bkp_read(MBOOT_BKP_RESET_FLAGS) ^ RESET_FLAGS_KEY) == mboot_port_bkp_read(MBOOT_BKP_RESET_FLAGS_CHECK);
}

void mboot_reset_flags_stash(uint32_t raw, uint32_t cause) {
    // The bootloader resets itself after a DFU session or an fsload. That software reset must not
    // replace the flags of the reset before it, which the application has not seen yet.
    if (cause == MBOOT_RESET_SOFT && reset_flags_kept()) {
        return;
    }
    mboot_port_bkp_write(MBOOT_BKP_RESET_FLAGS, raw);
    mboot_port_bkp_write(MBOOT_BKP_RESET_FLAGS_CHECK, raw ^ RESET_FLAGS_KEY);
}

uint32_t mboot_reset_flags_take(uint32_t current) {
    if (!reset_flags_kept()) {
        return current;
    }
    uint32_t raw = mboot_port_bkp_read(MBOOT_BKP_RESET_FLAGS);
    mboot_port_bkp_write(MBOOT_BKP_RESET_FLAGS, 0);
    mboot_port_bkp_write(MBOOT_BKP_RESET_FLAGS_CHECK, 0);
    return raw;
}
