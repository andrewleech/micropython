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

#ifndef MICROPY_INCLUDED_TESTS_MBOOT_HOST_GLUE_MBOOT_ASSERT_H
#define MICROPY_INCLUDED_TESTS_MBOOT_HOST_GLUE_MBOOT_ASSERT_H

// A failed assertion models the halt of a bare-metal bootloader: the sweep
// records it as an outcome and ends the simulated boot.
void host_assert_fail(const char *file, int line) __attribute__((noreturn));

#define assert(x) ((x) ? (void)0 : host_assert_fail(__FILE__, __LINE__))
#define ASSERT(x) assert(x)

#endif // MICROPY_INCLUDED_TESTS_MBOOT_HOST_GLUE_MBOOT_ASSERT_H
