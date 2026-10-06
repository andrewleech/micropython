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

#include <stdint.h>

#include "mcuboot_config/mcuboot_config.h"

int mcuboot_main(void);

// Bootloader info block, placed in the last 64 bytes of the boot area by mcuboot.ld. The
// application reads it for mcuboot.bootloader_info() and the layout check.
typedef struct {
    uint32_t magic;
    uint16_t info_version;
    uint16_t flags;         // bit0 DFU, bit1 fsload, bit2 gzip
    uint32_t layout_id;
    uint32_t api_version;
    char version[24];
    uint8_t reserved[24];
} mcuboot_bl_info_t;

_Static_assert(sizeof(mcuboot_bl_info_t) == 64, "bootloader info block size");

__attribute__((section(".mcuboot_info"), used)) const mcuboot_bl_info_t mcuboot_bl_info = {
    .magic = 0x4E49424Du,
    .info_version = 1,
    .flags = (MCUBOOT_DFU_ENABLE ? 1u : 0u) | (MCUBOOT_FSLOAD_ENABLE ? 2u : 0u)
        #if defined(MCUBOOT_FSLOAD_GZIP)
        | 4u
        #endif
    ,
    .layout_id = MCUBOOT_LAYOUT_ID,
    .api_version = MCUBOOT_API_VERSION,
    .version = MCUBOOT_BL_VERSION,
    .reserved = {[0 ... 23] = 0xFF},
};

int main(void) {
    return mcuboot_main();
}

// Entered from Reset_Handler in ports/stm32/resethandler.s, after .data and .bss have been
// initialised. The value of r0 at reset is not used.
void stm32_main(uint32_t r0) {
    (void)r0;
    main();
    for (;;) {
    }
}
