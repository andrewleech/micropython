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
#include "mboot_bl_info.h"
#include "mboot_port.h"
#include "mboot_keyhash_gen.h"

int mboot_main(void);

// The bootloader info block (format in mboot_bl_info.h), placed at the end of the boot area by
// the linker script.
__attribute__((section(".mboot_info"), used)) const mboot_bl_info_t mboot_bl_info = {
    .magic = MBOOT_BL_INFO_MAGIC,
    .info_version = MBOOT_BL_INFO_VERSION,
    .size = MBOOT_BL_INFO_DEFINED,
    .flags = (MBOOT_DFU_ENABLE ? MBOOT_BL_INFO_F_DFU : 0u)
        | (MBOOT_FSLOAD_ENABLE ? MBOOT_BL_INFO_F_FSLOAD : 0u)
        #if MICROPY_MBOOT_FSLOAD_GZIP
        | MBOOT_BL_INFO_F_GZIP
        #endif
        | (MBOOT_BL_INFO_UPDATE_NONE << MBOOT_BL_INFO_F_UPDATE_SHIFT),
    .layout_id = MBOOT_LAYOUT_ID,
    .api_version = MBOOT_API_VERSION,
    .version = MICROPY_MBOOT_BL_VERSION,
    .bl_image_id = MBOOT_SECCNT_ID_BOOTLOADER,
    // This bootloader build carries no security counter value.
    .bl_counter = 0,
    .key_hash = MBOOT_KEY_HASH,
    .reserved = {[0 ... sizeof(mboot_bl_info.reserved) - 1] = 0xFF},
};

// The port's reset or start-up code calls main() once .data and .bss are initialised.
int main(void) {
    return mboot_main();
}
