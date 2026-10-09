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

#include <stdbool.h>

#include "mboot_log.h"

// Output sink with a bounded buffer: characters past size - 1 are counted but dropped.
typedef struct {
    char *buf;
    size_t size;
    size_t n;
} out_t;

static void out_char(out_t *o, char c) {
    if (o->n + 1 < o->size) {
        o->buf[o->n] = c;
    }
    o->n++;
}

static void out_pad(out_t *o, char c, int count) {
    while (count-- > 0) {
        out_char(o, c);
    }
}

// Writes the digits of v in the given base, padded to width. The sign is written first when
// negative; with zero padding it goes before the zeros.
static void out_number(out_t *o, unsigned long v, bool negative, unsigned base, bool upper, int width, bool zero, bool left) {
    char digits[24];
    int ndigits = 0;
    do {
        unsigned d = (unsigned)(v % base);
        digits[ndigits++] = (char)(d < 10 ? '0' + d : (upper ? 'A' : 'a') + d - 10);
        v /= base;
    } while (v != 0);
    int total = ndigits + (negative ? 1 : 0);
    int pad = width > total ? width - total : 0;
    if (!left && !zero) {
        out_pad(o, ' ', pad);
    }
    if (negative) {
        out_char(o, '-');
    }
    if (!left && zero) {
        out_pad(o, '0', pad);
    }
    while (ndigits > 0) {
        out_char(o, digits[--ndigits]);
    }
    if (left) {
        out_pad(o, ' ', pad);
    }
}

int mboot_vsnprintf(char *buf, size_t size, const char *fmt, va_list ap) {
    out_t o = {buf, size, 0};

    for (; *fmt != '\0'; fmt++) {
        if (*fmt != '%') {
            out_char(&o, *fmt);
            continue;
        }
        const char *spec = fmt;
        fmt++;

        bool left = false;
        bool zero = false;
        for (;; fmt++) {
            if (*fmt == '-') {
                left = true;
            } else if (*fmt == '0') {
                zero = true;
            } else {
                break;
            }
        }
        int width = 0;
        while (*fmt >= '0' && *fmt <= '9') {
            width = width * 10 + (*fmt - '0');
            fmt++;
        }
        int prec = -1;
        if (*fmt == '.') {
            fmt++;
            prec = 0;
            while (*fmt >= '0' && *fmt <= '9') {
                prec = prec * 10 + (*fmt - '0');
                fmt++;
            }
        }
        bool is_long = false;
        bool is_size = false;
        if (*fmt == 'l') {
            is_long = true;
            fmt++;
        } else if (*fmt == 'z') {
            is_size = true;
            fmt++;
        }

        switch (*fmt) {
            case 'd':
            case 'i': {
                long v = is_long ? va_arg(ap, long) : (is_size ? (long)va_arg(ap, size_t) : (long)va_arg(ap, int));
                bool neg = v < 0;
                unsigned long mag = neg ? 0UL - (unsigned long)v : (unsigned long)v;
                out_number(&o, mag, neg, 10, false, width, zero, left);
                break;
            }
            case 'u':
            case 'x':
            case 'X': {
                unsigned long v = is_long ? va_arg(ap, unsigned long) : (is_size ? (unsigned long)va_arg(ap, size_t) : (unsigned long)va_arg(ap, unsigned int));
                out_number(&o, v, false, *fmt == 'u' ? 10 : 16, *fmt == 'X', width, zero, left);
                break;
            }
            case 'p': {
                uintptr_t v = (uintptr_t)va_arg(ap, void *);
                out_char(&o, '0');
                out_char(&o, 'x');
                out_number(&o, (unsigned long)v, false, 16, false, 0, false, false);
                break;
            }
            case 'c': {
                char c = (char)va_arg(ap, int);
                if (!left) {
                    out_pad(&o, ' ', width - 1);
                }
                out_char(&o, c);
                if (left) {
                    out_pad(&o, ' ', width - 1);
                }
                break;
            }
            case 's': {
                const char *s = va_arg(ap, const char *);
                if (s == NULL) {
                    s = "(null)";
                }
                int len = 0;
                while (s[len] != '\0' && (prec < 0 || len < prec)) {
                    len++;
                }
                if (!left) {
                    out_pad(&o, ' ', width - len);
                }
                for (int i = 0; i < len; i++) {
                    out_char(&o, s[i]);
                }
                if (left) {
                    out_pad(&o, ' ', width - len);
                }
                break;
            }
            case '%':
                out_char(&o, '%');
                break;
            default:
                // Unknown conversion: copy the specification text unchanged and stop
                // consuming arguments for it.
                for (; spec <= fmt && *spec != '\0'; spec++) {
                    out_char(&o, *spec);
                }
                if (*fmt == '\0') {
                    fmt--;
                }
                break;
        }
    }

    if (size > 0) {
        buf[o.n < size ? o.n : size - 1] = '\0';
    }
    return (int)o.n;
}
