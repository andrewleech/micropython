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

#ifndef MICROPY_INCLUDED_SHARED_TINYUSB_MBOOT_MBOOT_ELEM_H
#define MICROPY_INCLUDED_SHARED_TINYUSB_MBOOT_MBOOT_ELEM_H

// mboot_elem.h -- TLV element parser for shared/tinyusb/mboot
//
// Element stream format:
//   Each element is: <type:u8> <length:u8> <payload:u8[length]>
//   The stream is terminated by a MBOOT_ELEM_TYPE_END element whose length
//   field must be zero.  Any other value for the END element's length is
//   treated as a malformed stream.
//
// Byte-length cap: the u8 length field limits individual payloads to 255
// bytes.  This matches the existing ports/stm32/mboot format.  If a future
// type requires a longer payload, a new type code with a 2-byte length field
// should be defined; the parser dispatches on type so the two formats can
// coexist in the same stream without breaking backward compatibility.
//
// Streaming-mode (reading from flash without a RAM copy): not implemented in
// Stage 0.  The in-RAM API matches how ports/stm32/mboot uses the element list
// (ports/stm32/mboot/main.c:1640-1645).  A streaming variant can be added as
// a future extension once a consumer exists.

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

// Element type codes.  Values mirror ELEM_TYPE_* in ports/stm32/mboot/mboot.h.
typedef enum {
    MBOOT_ELEM_TYPE_END = 0,
    MBOOT_ELEM_TYPE_MOUNT = 1,
    MBOOT_ELEM_TYPE_FSLOAD = 2,
    MBOOT_ELEM_TYPE_STATUS = 3,
    // Additional types will be defined as fsload and pack support lands in
    // later stages.
} mboot_elem_type_t;

// mboot_elem_search -- find the first element of the given type.
//
// Scans the element stream in [buf, buf+buf_len).  On a match, sets *out_len
// to the element's payload length and returns a pointer to the first payload
// byte.
//
// Returns NULL if:
//   - buf is NULL or buf_len is 0
//   - the stream is truncated (header bytes fall outside buf_len)
//   - a declared payload length would extend past buf_len
//   - a MBOOT_ELEM_TYPE_END element is reached before the type is found
//   - a MBOOT_ELEM_TYPE_END element is found with a non-zero length field
//
// Note: the function returns as soon as a matching element is found and does
// not require a well-formed END terminator to follow. Callers that also need
// to verify stream integrity should call mboot_elem_validate separately.
//
// out_len may be NULL if the caller does not need the payload length.
const uint8_t *mboot_elem_search(const uint8_t *buf, size_t buf_len,
    mboot_elem_type_t type, uint8_t *out_len);

// mboot_elem_validate -- check that the stream is well-formed.
//
// Walks the entire stream confirming:
//   1. Every element header (2 bytes) fits within buf_len.
//   2. Every declared payload fits within buf_len.
//   3. The stream ends with MBOOT_ELEM_TYPE_END whose length field is zero.
//
// Returns true for a valid stream, false for any malformed input.
bool mboot_elem_validate(const uint8_t *buf, size_t buf_len);

#endif // MICROPY_INCLUDED_SHARED_TINYUSB_MBOOT_MBOOT_ELEM_H
