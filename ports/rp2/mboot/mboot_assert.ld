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

/*
 * mboot_assert.ld — supplemental linker assertions for the rp2 mboot slot.
 *
 * The pico-sdk PICO_FLASH_SIZE_BYTES variable has no effect after
 * pico_sdk_init() has already captured the board default, so the generated
 * pico_flash_region.ld reflects the full chip flash size, not the 64 KiB
 * mboot slot. The runtime slot guard (mboot_port_flash_is_writable) prevents
 * mboot from erasing itself, but the linker would silently place .text and
 * .rodata beyond 64 KiB if the image grew. This ASSERT catches that at
 * link time using the __flash_binary_end symbol emitted by pico-sdk's standard
 * linker script and the __mboot_end defsym defined in CMakeLists.txt.
 */
ASSERT(__flash_binary_end <= __mboot_end,
    "mboot image exceeds 64 KiB reserved slot — reduce code size or increase MBOOT_RESERVED")
