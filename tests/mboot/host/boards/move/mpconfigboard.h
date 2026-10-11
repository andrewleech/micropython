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

// The plain flash with swap using move: the primary slot is one erase unit larger than the
// secondary slot, which is as large as the primary slot without the unit that the move needs.
// Rollback protection is the security counter: with only a version check, an interrupted revert
// looks like an upgrade to an older version and is refused, so the new image stays.

#define MBOOT_PRIMARY_SIZE (0x40000)
#define MBOOT_SECONDARY_ADDR (0x90080000)
#define MBOOT_ROLLBACK_COUNTER (1)
#define MBOOT_SWAP_MODE (MBOOT_SWAP_MODE_SEL_MOVE)
#define MBOOT_DFU (0)
#define MBOOT_TMPBUF_SZ (256)
