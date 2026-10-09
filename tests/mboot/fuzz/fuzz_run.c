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

#include "fuzz_common.h"

#include "flash_map_backend/flash_map_backend.h"
#include "stub_fuzz.h"
#include "sysflash/sysflash.h"

// Input: [u8 flags][u16 elems_len][element stream][device payload].
//
// Runs mboot_fsload_run() on the element stream, with the payload as the contents of device 0
// (address 0x90000000; bytes beyond the payload read as erased). The build selects the update
// policy (FUZZ_POLICY_SINGLE, or swap using offset by default). The validator is the structural
// double in stub_fuzz.c, so a request whose file is a structurally valid image reaches pass 2 and
// writes the slot in RAM.

#define MAGIC 0x96f3b83du

int LLVMFuzzerTestOneInput(const uint8_t *data, size_t size) {
    if (size < 3) {
        return 0;
    }
    uint32_t flags = data[0];
    uint32_t elems_len = data[1] | (uint32_t)data[2] << 8;
    if (elems_len > size - 3 || elems_len > MBOOT_REQ_ELEMS_MAX) {
        return 0;
    }
    const uint8_t *elems = data + 3;
    const uint8_t *payload = elems + elems_len;
    size_t payload_len = size - 3 - elems_len;

    host_fs_set(payload, payload_len);
    host_slots_reset();
    memset(&stub_state, 0, sizeof(stub_state));
    stub_state.pending_fail = (flags >> 1) & 1;

    // Copy the element stream so an overread by the parser is caught by ASan.
    uint8_t *copy = malloc(elems_len ? elems_len : 1);
    FUZZ_CHECK(copy != NULL);
    memcpy(copy, elems, elems_len);
    int r = mboot_fsload_run(copy, elems_len);
    free(copy);

    const host_flash_stats_t *st = host_flash_stats();
    FUZZ_CHECK(st->violations == 0);
    if (r == MBOOT_RES_OK) {
        #if defined(FUZZ_POLICY_SINGLE)
        FUZZ_CHECK(memcmp(host_slots() + HOST_PRIMARY_OFF, "\x3d\xb8\xf3\x96", 4) == 0);
        #else
        FUZZ_CHECK(memcmp(host_slots() + HOST_SECONDARY_OFF + HOST_ERASE, "\x3d\xb8\xf3\x96", 4) == 0);
        FUZZ_CHECK(stub_state.pending_calls == 1);
        #endif
    }
    (void)MAGIC;
    return 0;
}
