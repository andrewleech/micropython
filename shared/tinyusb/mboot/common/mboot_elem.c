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

#include "mboot_elem.h"

// Elements are of the form: (type:u8, len:u8, payload:u8[len])
// The stream is terminated by MBOOT_ELEM_TYPE_END with len == 0.

// Advance *pp past the current element header, returning the payload pointer.
// Returns NULL (without modifying *pp) if the header or payload would overrun end.
static const uint8_t *next_elem(const uint8_t **pp, const uint8_t *end,
    uint8_t *out_type, uint8_t *out_len) {
    const uint8_t *p = *pp;
    size_t remaining = (size_t)(end - p);
    if (remaining < 2) {
        return NULL;  // truncated header
    }
    uint8_t elem_type = p[0];
    uint8_t elem_len = p[1];
    if ((size_t)elem_len > remaining - 2) {
        return NULL;  // payload overruns buffer
    }
    *out_type = elem_type;
    *out_len = elem_len;
    *pp = p + 2 + elem_len;
    return p + 2;
}

const uint8_t *mboot_elem_search(const uint8_t *buf, size_t buf_len,
    mboot_elem_type_t type, uint8_t *out_len) {
    if (buf == NULL || buf_len == 0) {
        return NULL;
    }
    const uint8_t *p = buf;
    const uint8_t *end = buf + buf_len;
    for (;;) {
        uint8_t elem_type, elem_len;
        const uint8_t *payload = next_elem(&p, end, &elem_type, &elem_len);
        if (payload == NULL) {
            return NULL;
        }
        if (elem_type == MBOOT_ELEM_TYPE_END) {
            // END must have length 0; any other value is malformed.
            return NULL;
        }
        if (elem_type == (uint8_t)type) {
            if (out_len != NULL) {
                *out_len = elem_len;
            }
            return payload;
        }
    }
}

bool mboot_elem_validate(const uint8_t *buf, size_t buf_len) {
    if (buf == NULL || buf_len == 0) {
        return false;
    }
    const uint8_t *p = buf;
    const uint8_t *end = buf + buf_len;
    for (;;) {
        uint8_t elem_type, elem_len;
        const uint8_t *payload = next_elem(&p, end, &elem_type, &elem_len);
        if (payload == NULL) {
            return false;
        }
        if (elem_type == MBOOT_ELEM_TYPE_END) {
            // Valid terminator requires length == 0.
            return elem_len == 0;
        }
    }
}
