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

#ifndef MICROPY_INCLUDED_SHARED_TINYUSB_MBOOT_MBOOT_PACK_H
#define MICROPY_INCLUDED_SHARED_TINYUSB_MBOOT_MBOOT_PACK_H

// mboot_pack.h — optional pack/unpack hook API for shared/tinyusb/mboot
//
// Stage 0 defines the hook surface only.  The implementation (libhydrogen-based
// signature verification, decryption, and decompression) is a Stage 3 deliverable
// supplied in a separate mboot_pack.c file.
//
// Build-time enable
// -----------------
// Set MBOOT_ENABLE_PACKING=1 in the port Makefile to activate packing.  The
// default is 0 (disabled).  When disabled, mboot_dfu.c calls mboot_region_write
// directly and this header is never included.
//
// This macro uses the same spelling as the stm32 port
// (ports/stm32/mboot/Makefile, pack.h, dfu.h, main.c) so that cross-port
// references are unambiguous.
//
// When MBOOT_ENABLE_PACKING=1 and no mboot_pack.c is linked, the build fails at
// link time with an undefined reference to mboot_pack_write.  This is intentional:
// it proves the hook is wired and that Stage 3 has something to satisfy.
//
// Chunk reassembly contract
// -------------------------
// mboot_pack_write is called once per DFU_DNLOAD payload.  Payloads arrive in
// wTransferSize-bounded slices and may be sub-chunk-sized relative to the
// internal pack chunk format.  The implementation is responsible for buffering
// incoming slices, reassembling complete chunks, verifying the chunk signature,
// decrypting and decompressing the payload, and writing the resulting plaintext
// to flash via mboot_region_write.  The caller (mboot_dfu.c) does not assume
// one-block-in, one-block-out semantics.
//
// Compile constraints: libc + mboot_api.h only.  No py/, extmod/,
// shared/runtime/, tusb.h, or port-specific headers.
//
// When MBOOT_ENABLE_PACKING=1, Stage 3 may add:
//   #include "lib/libhydrogen/hydrogen.h"
// inside the implementation file.  This header intentionally does not include
// hydrogen.h; Stage 0 never builds with MBOOT_ENABLE_PACKING=1.

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "mboot_api.h"

// ---------------------------------------------------------------------------
// Build-time flag
// ---------------------------------------------------------------------------

// MBOOT_ENABLE_PACKING — set to 1 in the port Makefile to enable pack support.
// Stage 0 always builds with this 0; Stage 3 wires up the implementation.
#ifndef MBOOT_ENABLE_PACKING
#define MBOOT_ENABLE_PACKING (0)
#endif

#if MBOOT_ENABLE_PACKING

// ---------------------------------------------------------------------------
// Pack chunk format constants
// ---------------------------------------------------------------------------

// Header version written into every chunk header.  Stage 3 must write this
// value and reject any chunk with a different version.
#define MBOOT_PACK_HEADER_VERSION (1)

// libhydrogen context string used for both signing and secretbox operations.
// The implementation file must pass this to all hydro_sign_* and
// hydro_secretbox_* calls.  Declared here so board config can override it.
#ifndef MBOOT_PACK_HYDRO_CONTEXT
#define MBOOT_PACK_HYDRO_CONTEXT "mbootenc"
#endif

// Chunk type identifiers stored in the format field of the chunk header.
// Values mirror ports/stm32/mboot/pack.h for cross-port consistency.
typedef enum {
    MBOOT_PACK_CHUNK_META = 0,      // metadata chunk (version, board info)
    MBOOT_PACK_CHUNK_FULL_SIG = 1,  // full-image signature chunk
    MBOOT_PACK_CHUNK_FW_RAW = 2,    // raw (uncompressed) firmware data
    MBOOT_PACK_CHUNK_FW_GZIP = 3,   // gzip-compressed firmware data
} mboot_pack_chunk_type_t;

// Chunk header layout (packed; Stage 3 must use __attribute__((packed)) or
// manual serialisation when reading from the incoming DFU buffer).
//
// Byte offsets:
//   0      header_vers  — must equal MBOOT_PACK_HEADER_VERSION
//   1      format       — mboot_pack_chunk_type_t value
//   2..3   _pad         — reserved, must be zero
//   4..7   address      — destination flash address (LE u32)
//   8..11  length       — byte count of the following data payload (LE u32),
//                         excluding the trailing signature bytes
//
// The chunk buffer layout in memory is:
//   [header: 12 bytes][data: length bytes][signature: MBOOT_PACK_SIGN_BYTES bytes]
//
// Stage 3 defines the full chunk buffer struct after including hydrogen.h.

#define MBOOT_PACK_CHUNK_HEADER_SIZE (12)

// Signature size placeholder.  Stage 3 replaces this with hydro_sign_BYTES
// once hydrogen.h is available.  Declared as a preprocessor constant so it
// can be used in array size calculations without a hydrogen.h dependency.
// The value 64 matches hydro_sign_BYTES in libhydrogen 0.1.
#ifndef MBOOT_PACK_SIGN_BYTES
#define MBOOT_PACK_SIGN_BYTES (64)
#endif

// Public-key size: 32 bytes (hydro_sign_PUBLICKEYBYTES in libhydrogen).
#ifndef MBOOT_PACK_SIGN_PUBLIC_KEY_BYTES
#define MBOOT_PACK_SIGN_PUBLIC_KEY_BYTES (32)
#endif

// ---------------------------------------------------------------------------
// Key storage (extern symbols; defined in board config or mboot_pack.c)
// ---------------------------------------------------------------------------

// Public signing key used to verify the per-chunk signature.
// Size must be hydro_sign_PUBLICKEYBYTES (32 bytes in libhydrogen).
extern const uint8_t mboot_pack_sign_public_key[MBOOT_PACK_SIGN_PUBLIC_KEY_BYTES];

// Symmetric key used for secretbox decryption of firmware chunks.
// Size must be hydro_secretbox_KEYBYTES (32 bytes in libhydrogen).
extern const uint8_t mboot_pack_secretbox_key[];

// ---------------------------------------------------------------------------
// Lifecycle
// ---------------------------------------------------------------------------

// mboot_pack_init — initialise pack state.
//
// Must be called once after mboot_region_init and before any DFU_DNLOAD
// transfer.  Initialises the internal chunk reassembly buffer and any
// libhydrogen state.
//
// Return: 0 on success, negative errno-style value on error.  This
// deliberately differs from ports/stm32/mboot/pack.c (void) because
// libhydrogen's hydro_init() can fail; a nonzero return must abort the
// bootloader before entering DFU mode.
int mboot_pack_init(void);

// ---------------------------------------------------------------------------
// Write hook
// ---------------------------------------------------------------------------

// mboot_pack_write — route a DFU_DNLOAD payload through the unpack pipeline.
//
// Called by mboot_dfu.c instead of mboot_region_write when MBOOT_ENABLE_PACKING
// is non-zero.  The implementation buffers incoming slices, reassembles
// complete pack chunks, verifies the chunk signature, decrypts and
// decompresses the plaintext, then calls mboot_region_write with the result.
//
// One mboot_pack_write call corresponds to one DFU_DNLOAD wTransferSize
// payload.  Multiple calls may be required to accumulate a full chunk; the
// implementation owns the reassembly state.
//
// Arguments
//   addr     — destination flash address for this payload slice, as derived
//              from wBlockNum by block_to_addr() in mboot_dfu.c.
//   src      — payload bytes from the DFU_DNLOAD buffer (4-byte aligned,
//              padded to a multiple of 4 bytes by mboot_dfu.c).
//   len      — byte count of src (multiple of 4).
//   dry_run  — if true, validate the chunk (signature/decrypt check) but do
//              not call mboot_region_write.  Mirrors the stm32 do_write()
//              dry_run parameter: exercises the crypto path without committing
//              to flash.
//
// Return: 0 on success, negative errno-style value on error (signature
// mismatch, decryption failure, out-of-range address, flash write error).
int mboot_pack_write(mboot_addr_t addr, const uint8_t *src, size_t len, bool dry_run);

// ---------------------------------------------------------------------------
// Upload guard (note)
// ---------------------------------------------------------------------------
//
// DFU_UPLOAD is refused unconditionally when MBOOT_ENABLE_PACKING=1.  This
// matches the ports/stm32/mboot/main.c do_read() pattern: the handler sets
// DFU_STATUS_ERROR_FILE and returns an error string rather than reading back
// encrypted ciphertext.  No separate mboot_pack_upload_allowed() predicate is
// provided; mboot_dfu.c implements the refusal inline under the same
// #if MBOOT_ENABLE_PACKING guard.

#endif // MBOOT_ENABLE_PACKING

#endif // MICROPY_INCLUDED_SHARED_TINYUSB_MBOOT_MBOOT_PACK_H
