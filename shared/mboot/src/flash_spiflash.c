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

#include <errno.h>

#include "mboot_spiflash.h"

int mboot_spiflash_read(mp_spiflash_t *spif, uint32_t off, void *dst, uint32_t len) {
    return mp_spiflash_read(spif, off, len, dst);
}

int mboot_spiflash_write(mp_spiflash_t *spif, uint32_t off, const void *src, uint32_t len) {
    return mp_spiflash_write(spif, off, len, src);
}

// A power cut during an erase isn't atomic on NOR flash: any part of the block may end up
// erased. bootutil assumes it is atomic. It scrubs a slot trailer from its last sector
// backwards, so the magic (the last 16 bytes of the slot) goes before the flags, and it takes a
// trailer with a valid magic and no flags for a new request. So the last bytes of each block are
// programmed to zero before the block is erased, and blocks are erased from the top. An
// interrupted erase then never leaves a valid magic next to flags that are already gone.
#define MAGIC_LEN (16)

int mboot_spiflash_erase(mp_spiflash_t *spif, uint32_t off, uint32_t len) {
    if (((off | len) & (MP_SPIFLASH_ERASE_BLOCK_SIZE - 1)) != 0) {
        return -EINVAL;
    }
    static const uint8_t zeros[MAGIC_LEN];
    for (uint32_t end = off + len; end > off; end -= MP_SPIFLASH_ERASE_BLOCK_SIZE) {
        uint32_t block = end - MP_SPIFLASH_ERASE_BLOCK_SIZE;
        int ret = mp_spiflash_write(spif, end - MAGIC_LEN, MAGIC_LEN, zeros);
        if (ret == 0) {
            ret = mp_spiflash_erase_block(spif, block);
        }
        if (ret != 0) {
            return ret;
        }
    }
    return 0;
}
