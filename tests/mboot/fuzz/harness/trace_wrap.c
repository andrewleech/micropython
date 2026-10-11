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
#include <stdio.h>

// Access trace of the stream area, to measure how bootutil reads a file. Linked with
// -Wl,--wrap=mboot_stream_area_read (make measure).

int __real_mboot_stream_area_read(uint32_t off, void *dst, uint32_t len);

#define MAX_TRACE 4096

static struct {
    uint32_t off, len;
} trace[MAX_TRACE];
static unsigned count;

int __wrap_mboot_stream_area_read(uint32_t off, void *dst, uint32_t len) {
    if (count < MAX_TRACE) {
        trace[count].off = off;
        trace[count].len = len;
    }
    count++;
    return __real_mboot_stream_area_read(off, dst, len);
}

void trace_reset(void) {
    count = 0;
}

// Prints the reads that go backward (offset below the end of the previous read), and the total.
void trace_report(const char *name) {
    unsigned backward = 0;
    uint32_t end = 0;
    printf("  trace %s: %u reads\n", name, count);
    for (unsigned i = 0; i < count && i < MAX_TRACE; ++i) {
        if (trace[i].off < end) {
            backward++;
            printf("    backward read %u: offset %u length %u (previous read ended at %u)\n", i, (unsigned)trace[i].off,
                (unsigned)trace[i].len, (unsigned)end);
        }
        end = trace[i].off + trace[i].len;
    }
    printf("  trace %s: %u backward reads\n", name, backward);
}
