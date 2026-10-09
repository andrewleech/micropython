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

#ifndef MICROPY_INCLUDED_SHARED_MBOOT_MBOOT_LAYOUT_H
#define MICROPY_INCLUDED_SHARED_MBOOT_MBOOT_LAYOUT_H

// The MCUboot configuration of a build, derived from two headers found through the include path:
//
//   mpconfigboard.h   the board: slot placement and the choices below
//   mboot_dev.h     the port: geometry of the flash devices (MBOOT_DEV0_*, MBOOT_DEV1_*)
//
// This header only holds numbers and macros, so tools/mboot_gen.py can run it through the C
// preprocessor to get the same values for the linker scripts, the image signing and the
// Makefiles. All addresses are CPU addresses (DFU addresses for a device that is not memory
// mapped).
//
// Devices (mboot_dev.h). Device 0 is the internal flash and holds the bootloader, device 1 is
// optional (a SPI flash). Each has MBOOT_DEVn_NAME, _BASE, _SIZE, _WRITE (program unit, at most
// 32 bytes), _ERASED_VAL, _MAPPED and _ECC, and gives its erase units one of two ways:
//   MBOOT_DEVn_ERASE          equal erase units over the whole device
//   MBOOT_DEVn_RUNS           k runs (1 to 4) of equal erase units, one behind the other from the
//                               device base: MBOOT_DEVn_RUNj_SIZE and MBOOT_DEVn_RUNj_ERASE for
//                               j below k. The sizes add up to MBOOT_DEVn_SIZE, and a run starts
//                               at, and is a multiple of, its own erase unit.
// The port also gives MBOOT_BOOT_SIZE (the bootloader region at the start of device 0, unless the
// board defines it first), MBOOT_VTOR_ALIGN and the RAM symbols.
//
// Erase units belong to areas. Every area (the bootloader region, the slots, the counter, the
// shadow, scratch and intent areas and the filesystem) lies in one run of its device, starts at
// and is a multiple of the erase unit of that run (MBOOT_<AREA>_UNIT), and overlaps no other
// area. The primary slot, the secondary slot and the scratch and shadow areas have one erase
// unit, because bootutil exchanges the slots sector by sector.
//
// Board inputs. Sizes are in bytes, a size of an area is a multiple of its erase unit.
//   MBOOT_PRIMARY_SIZE        required, slot the CPU boots from
//   MBOOT_SECONDARY_ADDR      required except for MBOOT_POLICY_SEL_SINGLE (not allowed there), update
//                               slot. Its size follows from the policy and the swap mode: one erase
//                               unit larger than the primary slot for swap using offset, one erase
//                               unit smaller for swap using move, as large otherwise
//   MBOOT_ROLLBACK_COUNTER    required, 1 keeps a monotonic security counter (in flash, or kept by
//                               the port with MBOOT_SECCNT_PORT; use this on a product), 0 only
//                               refuses an older version
//   MBOOT_PRIMARY_ADDR        default: directly after the bootloader region
//   MBOOT_BOOT_SIZE           bootloader region at the start of device 0 (default of the port); it
//                               lies in one run and is a number of its erase units
//   MBOOT_HEADER_SIZE         image header (default 0x400)
//   MBOOT_SECURITY_COUNTER    value signed into the image (default 0)
//   MBOOT_SECCNT_ADDR         security counter in flash, two erase units (default: after the primary slot)
//   MBOOT_SHADOW_ADDR         shadow words of a device 0 with ECC (default: after the counter)
//   MBOOT_FS_ADDR, _SIZE      the application filesystem, default the flash between the
//                               auxiliary areas and the secondary slot
//   MBOOT_DFU                 0 or 1, DFU front end (default 1)
//   MBOOT_DFU_VID, _PID       USB ids of the bootloader (default MICROPY_HW_USB_VID or 0xF055, and 0xDFA5)
//   MBOOT_DFU_PRODUCT         USB product string (default board name + " MCUboot")
//   MBOOT_DFU_TIMEOUT_S       inactivity timeout of a requested recovery, 0 for none (default 120)
//   MBOOT_LOG_LEVEL           0 off to 4 debug (default 3)
//   MBOOT_TMPBUF_SZ           hash read chunk (default 4096)
//
// Choices (defaults in brackets). The alternatives of MBOOT_POLICY, MBOOT_SWAP_MODE and
// MBOOT_CRYPTO are the constants of the same prefix with _SEL_ in the name, which this header
// defines before it reads the board.
//   MBOOT_POLICY              MBOOT_POLICY_SEL_SWAP: two slots, a new image is exchanged with
//                                 the running one and reverts unless it is confirmed (default)
//                               MBOOT_POLICY_SEL_OVERWRITE_EXTERNAL: two slots of equal size, a new
//                                 image is copied over the primary slot; no revert; the secondary slot
//                                 may be on device 1
//                               MBOOT_POLICY_SEL_SINGLE: one slot that is overwritten in place; no
//                                 revert; needs MBOOT_ROLLBACK_COUNTER 1; with fsload an intent area.
//                                 The slot holds no swap state, so an image may fill it up to the
//                                 trailer reservation of MBOOT_TRAILER_SIZE bytes at its end
//   MBOOT_SWAP_MODE           with the swap policy: MBOOT_SWAP_MODE_SEL_OFFSET (default),
//                                 MBOOT_SWAP_MODE_SEL_MOVE or MBOOT_SWAP_MODE_SEL_SCRATCH
//   MBOOT_SCRATCH_SIZE        scratch area of swap using scratch, a multiple of its erase unit
//                               (default one erase unit)
//   MBOOT_SCRATCH_ADDR, MBOOT_INTENT_ADDR
//                               placement of the scratch area and of the intent area (of the
//                               single policy with fsload; one erase unit), on device 0
//   MBOOT_CRYPTO              MBOOT_CRYPTO_SEL_TINYCRYPT (default) or MBOOT_CRYPTO_SEL_MBEDTLS
//                               (the port provides the mbedtls sources and configuration)
//   MBOOT_FIH_LEVEL           fault injection hardening: 0 off, 1 low, 2 medium, 3 high; 3 needs
//                               mboot_port_entropy_u8() (default 0)
//   MBOOT_SECCNT_PORT         1 keeps the security counter in the port (mboot_port_seccnt_*)
//                               instead of in flash; needs MBOOT_ROLLBACK_COUNTER 1
//   MBOOT_VERSION_CHECK       with MBOOT_ROLLBACK_COUNTER 0: 0 disables the refusal of an older
//                               version, so that there is no rollback protection at all (default 1)
//   MBOOT_CONFIRM_AUTO        1 confirms a new image after boot.py has run (default 0)
//   MBOOT_HASH_DIRECT         1 hashes an image in place instead of through a read buffer; needs
//                               memory mapped slot devices, not with swap using offset or fsload
//   MBOOT_VALIDATE_PRIMARY    0 starts the primary slot without a signature check, so that an
//                               application that can write that slot bypasses the signature; refused
//                               with the single policy and with MBOOT_PRODUCTION=1 (default 1)
//   MBOOT_FSLOAD_FAT, _LFS2, _RAW
//                               define as 1 to read an update from a FAT or littlefs2 filesystem, or
//                               from a raw window of flash
//   MBOOT_FSLOAD_GZIP         define as 1 to accept a gzip compressed update file
//
// Placement. The counter, shadow, scratch and intent areas follow the primary slot in that order,
// each one directly behind the one before unless the board names its address, and are on device 0.
// The default of the filesystem is behind them, except that a scratch or intent area that the
// board places leaves it where it would be without. A secondary slot or filesystem on device 1 (a SPI flash) is
// allowed, the other areas are not. Device 1 has no ECC and its erase units are a multiple of the
// 4 KiB erase block; a secondary slot on it needs the erase unit of the primary slot and a write
// unit equal to the trailer alignment.

#define MBOOT_API_VERSION 1u

// Alternatives of the choices above, defined before the board is read so that mpconfigboard.h can
// name them.
#define MBOOT_POLICY_SEL_SWAP 1
#define MBOOT_POLICY_SEL_OVERWRITE_EXTERNAL 2
#define MBOOT_POLICY_SEL_SINGLE 3
#define MBOOT_SWAP_MODE_SEL_OFFSET 1
#define MBOOT_SWAP_MODE_SEL_MOVE 2
#define MBOOT_SWAP_MODE_SEL_SCRATCH 3
#define MBOOT_CRYPTO_SEL_TINYCRYPT 1
#define MBOOT_CRYPTO_SEL_MBEDTLS 2

#include "mpconfigboard.h"
#include "mboot_dev.h"

// ---- inputs ----

#ifndef MBOOT_HEADER_SIZE
#define MBOOT_HEADER_SIZE 0x400u
#endif
#ifndef MBOOT_TLV_RESERVE
#define MBOOT_TLV_RESERVE 0x400u
#endif
#ifndef MBOOT_SECURITY_COUNTER
#define MBOOT_SECURITY_COUNTER 0
#endif
#ifndef MBOOT_DFU
#define MBOOT_DFU 1
#endif
#ifndef MBOOT_DFU_VID
#if defined(MICROPY_HW_USB_VID)
#define MBOOT_DFU_VID MICROPY_HW_USB_VID
#else
#define MBOOT_DFU_VID 0xF055
#endif
#endif
#ifndef MBOOT_DFU_PID
#define MBOOT_DFU_PID 0xDFA5
#endif
#ifndef MBOOT_DFU_PRODUCT
#if defined(MICROPY_HW_BOARD_NAME)
#define MBOOT_DFU_PRODUCT MICROPY_HW_BOARD_NAME " Mboot"
#else
#define MBOOT_DFU_PRODUCT "MicroPython Mboot"
#endif
#endif
#ifndef MBOOT_DFU_TIMEOUT_S
#define MBOOT_DFU_TIMEOUT_S 120
#endif
#ifndef MBOOT_LOG_LEVEL
#define MBOOT_LOG_LEVEL 3
#endif
#ifndef MBOOT_TMPBUF_SZ
#define MBOOT_TMPBUF_SZ 4096
#endif

#define MBOOT_DFU_ENABLE (MBOOT_DFU)

// ---- feature selection ----

// A feature macro that a board defines to 0 is the same as one that it does not define: below,
// a feature is on when its macro is defined (to 1).
#if defined(MBOOT_FSLOAD_FAT) && !(MBOOT_FSLOAD_FAT)
#undef MBOOT_FSLOAD_FAT
#endif
#if defined(MBOOT_FSLOAD_LFS2) && !(MBOOT_FSLOAD_LFS2)
#undef MBOOT_FSLOAD_LFS2
#endif
#if defined(MBOOT_FSLOAD_RAW) && !(MBOOT_FSLOAD_RAW)
#undef MBOOT_FSLOAD_RAW
#endif
#if defined(MBOOT_FSLOAD_GZIP) && !(MBOOT_FSLOAD_GZIP)
#undef MBOOT_FSLOAD_GZIP
#endif
#if defined(MBOOT_CONFIRM_AUTO) && !(MBOOT_CONFIRM_AUTO)
#undef MBOOT_CONFIRM_AUTO
#endif
#if defined(MBOOT_SECCNT_PORT) && !(MBOOT_SECCNT_PORT)
#undef MBOOT_SECCNT_PORT
#endif

// Slot policy. MBOOT_POLICY_SWAP, _OVERWRITE_EXTERNAL and _SINGLE are always defined, to 0 or 1.
#ifndef MBOOT_POLICY
#define MBOOT_POLICY (MBOOT_POLICY_SEL_SWAP)
#endif
#if MBOOT_POLICY == MBOOT_POLICY_SEL_SWAP
#define MBOOT_POLICY_SWAP 1
#define MBOOT_POLICY_OVERWRITE_EXTERNAL 0
#define MBOOT_POLICY_SINGLE 0
#elif MBOOT_POLICY == MBOOT_POLICY_SEL_OVERWRITE_EXTERNAL
#define MBOOT_POLICY_SWAP 0
#define MBOOT_POLICY_OVERWRITE_EXTERNAL 1
#define MBOOT_POLICY_SINGLE 0
#elif MBOOT_POLICY == MBOOT_POLICY_SEL_SINGLE
#define MBOOT_POLICY_SWAP 0
#define MBOOT_POLICY_OVERWRITE_EXTERNAL 0
#define MBOOT_POLICY_SINGLE 1
#else
#error "MBOOT_POLICY is MBOOT_POLICY_SEL_SWAP, MBOOT_POLICY_SEL_OVERWRITE_EXTERNAL or MBOOT_POLICY_SEL_SINGLE"
#endif

// The algorithm that moves an update into place.
#if MBOOT_POLICY_SWAP
#ifndef MBOOT_SWAP_MODE
#define MBOOT_SWAP_MODE (MBOOT_SWAP_MODE_SEL_OFFSET)
#endif
#if MBOOT_SWAP_MODE == MBOOT_SWAP_MODE_SEL_OFFSET
#define MCUBOOT_SWAP_USING_OFFSET 1
// Lets an install into the secondary slot revive a device whose primary slot header is erased.
#define MCUBOOT_BOOTSTRAP 1
#elif MBOOT_SWAP_MODE == MBOOT_SWAP_MODE_SEL_MOVE
#define MCUBOOT_SWAP_USING_MOVE 1
#elif MBOOT_SWAP_MODE == MBOOT_SWAP_MODE_SEL_SCRATCH
#define MCUBOOT_SWAP_USING_SCRATCH 1
#else
#error "MBOOT_SWAP_MODE is MBOOT_SWAP_MODE_SEL_OFFSET, MBOOT_SWAP_MODE_SEL_MOVE or MBOOT_SWAP_MODE_SEL_SCRATCH"
#endif
#else
#if defined(MBOOT_SWAP_MODE)
#error "MBOOT_SWAP_MODE only applies to MBOOT_POLICY_SEL_SWAP"
#endif
#if MBOOT_POLICY_OVERWRITE_EXTERNAL
#define MCUBOOT_OVERWRITE_ONLY 1
#define MCUBOOT_OVERWRITE_ONLY_FAST 1
#else
#define MCUBOOT_SINGLE_APPLICATION_SLOT 1
#endif
#endif

// Signature and hash backend. The signature is always ECDSA P-256.
#ifndef MBOOT_CRYPTO
#define MBOOT_CRYPTO (MBOOT_CRYPTO_SEL_TINYCRYPT)
#endif
#if MBOOT_CRYPTO == MBOOT_CRYPTO_SEL_TINYCRYPT
#define MCUBOOT_USE_TINYCRYPT 1
#elif MBOOT_CRYPTO == MBOOT_CRYPTO_SEL_MBEDTLS
#define MCUBOOT_USE_MBED_TLS 1
#else
#error "MBOOT_CRYPTO is MBOOT_CRYPTO_SEL_TINYCRYPT or MBOOT_CRYPTO_SEL_MBEDTLS"
#endif

// Fault injection hardening.
#ifndef MBOOT_FIH_LEVEL
#define MBOOT_FIH_LEVEL 0
#endif
#if MBOOT_FIH_LEVEL == 0
#define MCUBOOT_FIH_PROFILE_OFF 1
#elif MBOOT_FIH_LEVEL == 1
#define MCUBOOT_FIH_PROFILE_LOW 1
#elif MBOOT_FIH_LEVEL == 2
#define MCUBOOT_FIH_PROFILE_MEDIUM 1
#elif MBOOT_FIH_LEVEL == 3
#define MCUBOOT_FIH_PROFILE_HIGH 1
#else
#error "MBOOT_FIH_LEVEL is 0 (off), 1 (low), 2 (medium) or 3 (high)"
#endif

// Rollback protection.
#if MBOOT_ROLLBACK_COUNTER
#if defined(MBOOT_VERSION_CHECK)
#error "MBOOT_VERSION_CHECK only applies to MBOOT_ROLLBACK_COUNTER 0"
#endif
#define MCUBOOT_HW_ROLLBACK_PROT 1
#if !defined(MBOOT_SECCNT_PORT)
#define MBOOT_SECCNT_FLASH 1
#endif
#else
#if defined(MBOOT_SECCNT_PORT)
#error "MBOOT_SECCNT_PORT needs MBOOT_ROLLBACK_COUNTER 1"
#endif
#if MBOOT_POLICY_SINGLE
#error "MBOOT_POLICY_SEL_SINGLE needs MBOOT_ROLLBACK_COUNTER 1: a version comparison with the primary slot cannot run after the only slot has been overwritten"
#endif
#ifndef MBOOT_VERSION_CHECK
#define MBOOT_VERSION_CHECK 1
#endif
#if MBOOT_VERSION_CHECK
#define MCUBOOT_DOWNGRADE_PREVENTION 1
#define MCUBOOT_DOWNGRADE_PREVENTION_SECURITY_COUNTER 0
#endif
#endif

// Hash the image in place.
#ifndef MBOOT_HASH_DIRECT
#define MBOOT_HASH_DIRECT 0
#endif
#if MBOOT_HASH_DIRECT
#define MCUBOOT_HASH_STORAGE_DIRECTLY 1
#endif

// Validation of the primary slot at every boot. Without it the bootloader starts whatever the
// primary slot holds.
#ifndef MBOOT_VALIDATE_PRIMARY
#define MBOOT_VALIDATE_PRIMARY 1
#endif
#if MBOOT_VALIDATE_PRIMARY
#define MCUBOOT_VALIDATE_PRIMARY_SLOT 1
#elif MBOOT_POLICY_SINGLE
#error "MBOOT_VALIDATE_PRIMARY 0 is not allowed with MBOOT_POLICY_SEL_SINGLE: the only slot is rewritten by DFU and fsload and has to be validated at every boot"
#elif defined(MBOOT_PRODUCTION) && MBOOT_PRODUCTION
#error "MBOOT_VALIDATE_PRIMARY 0 is refused in a production build (MBOOT_PRODUCTION=1): the primary slot would start without a signature check"
#else
#warning "MBOOT_VALIDATE_PRIMARY is 0: the primary slot starts without a signature check, an application that can write it bypasses secure boot"
#endif

// Update sources.
#if defined(MBOOT_FSLOAD_FAT) || defined(MBOOT_FSLOAD_LFS2) || defined(MBOOT_FSLOAD_RAW)
#define MBOOT_FSLOAD_ENABLE 1
#else
#define MBOOT_FSLOAD_ENABLE 0
#if defined(MBOOT_FSLOAD_GZIP)
#error "MBOOT_FSLOAD_GZIP needs a reader: MBOOT_FSLOAD_FAT, MBOOT_FSLOAD_LFS2 or MBOOT_FSLOAD_RAW"
#endif
#endif

// ---- bootutil configuration ----

#define MCUBOOT_SIGN_EC256 1
#define MCUBOOT_IMAGE_NUMBER 1
#define MCUBOOT_HAVE_LOGGING 1
#define MCUBOOT_HAVE_ASSERT_H 1
#define MCUBOOT_USE_FLASH_AREA_GET_SECTORS 1
#define MCUBOOT_BOOT_TMPBUF_SZ (MBOOT_TMPBUF_SZ)

// ---- devices ----

#if !defined(MBOOT_DEV0_BASE) || !defined(MBOOT_DEV0_SIZE) || !defined(MBOOT_DEV0_WRITE) \
    || !defined(MBOOT_DEV0_ERASED_VAL) || !defined(MBOOT_DEV0_MAPPED) || !defined(MBOOT_DEV0_ECC)
#error "mboot_dev.h does not describe device 0 (MBOOT_DEV0_BASE, _SIZE, _WRITE, _ERASED_VAL, _MAPPED, _ECC and its erase units)"
#endif
#if !defined(MBOOT_BOOT_SIZE)
#error "mboot_dev.h or the board has to define MBOOT_BOOT_SIZE"
#endif

// The secondary slot and the filesystem may be on device 1.
#if defined(MBOOT_DEV1_BASE)
#if !defined(MBOOT_DEV1_SIZE) || !defined(MBOOT_DEV1_WRITE) \
    || !defined(MBOOT_DEV1_ERASED_VAL) || !defined(MBOOT_DEV1_MAPPED) || !defined(MBOOT_DEV1_ECC)
#error "mboot_dev.h does not describe device 1 (MBOOT_DEV1_BASE, _SIZE, _WRITE, _ERASED_VAL, _MAPPED, _ECC and its erase units)"
#endif
#if MBOOT_DEV1_BASE < MBOOT_DEV0_BASE + MBOOT_DEV0_SIZE
#error "device 1 has to be above device 0"
#endif
#define MBOOT_DEV_OF(addr) ((addr) >= MBOOT_DEV1_BASE ? 1 : 0)
#define MBOOT_DEV_BASE(dev) ((dev) == 1 ? MBOOT_DEV1_BASE : MBOOT_DEV0_BASE)
#else
#define MBOOT_DEV_OF(addr) (0)
#define MBOOT_DEV_BASE(dev) (MBOOT_DEV0_BASE)
#endif

#if (MBOOT_DEV0_WRITE & (MBOOT_DEV0_WRITE - 1)) != 0 || MBOOT_DEV0_WRITE < 1
#error "the write unit of device 0 has to be a power of two"
#endif
#if defined(MBOOT_DEV1_BASE) && ((MBOOT_DEV1_WRITE & (MBOOT_DEV1_WRITE - 1)) != 0 || MBOOT_DEV1_WRITE < 1)
#error "the write unit of device 1 has to be a power of two"
#endif

// ---- erase units ----

// A run holds whole erase units that are a power of two and at least the write unit, and starts
// at a multiple of its erase unit.
#define MBOOT_RUN_OK(start, size, erase, write) \
    ((size) > 0 && (erase) >= (write) && ((erase) & ((erase) - 1)) == 0 && ((size) % (erase)) == 0 \
    && ((start) % (erase)) == 0)

#if defined(MBOOT_DEV0_RUNS)
#if defined(MBOOT_DEV0_ERASE)
#error "mboot_dev.h gives the erase units of device 0 twice: MBOOT_DEV0_ERASE and MBOOT_DEV0_RUNS"
#endif
#elif defined(MBOOT_DEV0_ERASE)
#define MBOOT_DEV0_RUNS 1
#define MBOOT_DEV0_RUN0_SIZE (MBOOT_DEV0_SIZE)
#define MBOOT_DEV0_RUN0_ERASE (MBOOT_DEV0_ERASE)
#else
#error "mboot_dev.h does not give the erase units of device 0 (MBOOT_DEV0_ERASE, or MBOOT_DEV0_RUNS with the _RUNj_SIZE and _RUNj_ERASE of every run)"
#endif
#if MBOOT_DEV0_RUNS < 1 || MBOOT_DEV0_RUNS > 4
#error "a device has one to four runs of erase units"
#endif
#if !defined(MBOOT_DEV0_RUN0_SIZE) || !defined(MBOOT_DEV0_RUN0_ERASE) \
    || (MBOOT_DEV0_RUNS > 1 && (!defined(MBOOT_DEV0_RUN1_SIZE) || !defined(MBOOT_DEV0_RUN1_ERASE))) \
    || (MBOOT_DEV0_RUNS > 2 && (!defined(MBOOT_DEV0_RUN2_SIZE) || !defined(MBOOT_DEV0_RUN2_ERASE))) \
    || (MBOOT_DEV0_RUNS > 3 && (!defined(MBOOT_DEV0_RUN3_SIZE) || !defined(MBOOT_DEV0_RUN3_ERASE)))
#error "mboot_dev.h does not give the size and the erase unit of every run of device 0"
#endif
// A run behind the last one has no bytes and the erase unit of the run before it.
#if MBOOT_DEV0_RUNS < 2
#define MBOOT_DEV0_RUN1_SIZE 0
#define MBOOT_DEV0_RUN1_ERASE MBOOT_DEV0_RUN0_ERASE
#endif
#if MBOOT_DEV0_RUNS < 3
#define MBOOT_DEV0_RUN2_SIZE 0
#define MBOOT_DEV0_RUN2_ERASE MBOOT_DEV0_RUN1_ERASE
#endif
#if MBOOT_DEV0_RUNS < 4
#define MBOOT_DEV0_RUN3_SIZE 0
#define MBOOT_DEV0_RUN3_ERASE MBOOT_DEV0_RUN2_ERASE
#endif
#define MBOOT_DEV0_RUN0_END (MBOOT_DEV0_RUN0_SIZE)
#define MBOOT_DEV0_RUN1_END (MBOOT_DEV0_RUN0_END + MBOOT_DEV0_RUN1_SIZE)
#define MBOOT_DEV0_RUN2_END (MBOOT_DEV0_RUN1_END + MBOOT_DEV0_RUN2_SIZE)
#define MBOOT_DEV0_RUN3_END (MBOOT_DEV0_RUN2_END + MBOOT_DEV0_RUN3_SIZE)
#if MBOOT_DEV0_RUN3_END != MBOOT_DEV0_SIZE
#error "the runs of device 0 do not add up to its size"
#endif
#if !MBOOT_RUN_OK(0, MBOOT_DEV0_RUN0_SIZE, MBOOT_DEV0_RUN0_ERASE, MBOOT_DEV0_WRITE) \
    || (MBOOT_DEV0_RUNS > 1 && !MBOOT_RUN_OK(MBOOT_DEV0_RUN0_END, MBOOT_DEV0_RUN1_SIZE, MBOOT_DEV0_RUN1_ERASE, MBOOT_DEV0_WRITE)) \
    || (MBOOT_DEV0_RUNS > 2 && !MBOOT_RUN_OK(MBOOT_DEV0_RUN1_END, MBOOT_DEV0_RUN2_SIZE, MBOOT_DEV0_RUN2_ERASE, MBOOT_DEV0_WRITE)) \
    || (MBOOT_DEV0_RUNS > 3 && !MBOOT_RUN_OK(MBOOT_DEV0_RUN2_END, MBOOT_DEV0_RUN3_SIZE, MBOOT_DEV0_RUN3_ERASE, MBOOT_DEV0_WRITE))
#error "every run of device 0 has to hold whole erase units that are a power of two and at least the write unit, and to start at a multiple of its erase unit"
#endif
#if MBOOT_DEV0_RUNS == 1
#define MBOOT_DEV0_ERASE_AT(o) (MBOOT_DEV0_RUN0_ERASE)
#define MBOOT_DEV0_RUN_END_AT(o) (MBOOT_DEV0_RUN0_END)
#else
#define MBOOT_DEV0_ERASE_AT(o) \
    ((o) < MBOOT_DEV0_RUN0_END ? MBOOT_DEV0_RUN0_ERASE : (o) < MBOOT_DEV0_RUN1_END ? MBOOT_DEV0_RUN1_ERASE \
    : (o) < MBOOT_DEV0_RUN2_END ? MBOOT_DEV0_RUN2_ERASE : MBOOT_DEV0_RUN3_ERASE)
#define MBOOT_DEV0_RUN_END_AT(o) \
    ((o) < MBOOT_DEV0_RUN0_END ? MBOOT_DEV0_RUN0_END : (o) < MBOOT_DEV0_RUN1_END ? MBOOT_DEV0_RUN1_END \
    : (o) < MBOOT_DEV0_RUN2_END ? MBOOT_DEV0_RUN2_END : MBOOT_DEV0_RUN3_END)
#endif

#if defined(MBOOT_DEV1_BASE)
#if defined(MBOOT_DEV1_RUNS)
#if defined(MBOOT_DEV1_ERASE)
#error "mboot_dev.h gives the erase units of device 1 twice: MBOOT_DEV1_ERASE and MBOOT_DEV1_RUNS"
#endif
#elif defined(MBOOT_DEV1_ERASE)
#define MBOOT_DEV1_RUNS 1
#define MBOOT_DEV1_RUN0_SIZE (MBOOT_DEV1_SIZE)
#define MBOOT_DEV1_RUN0_ERASE (MBOOT_DEV1_ERASE)
#else
#error "mboot_dev.h does not give the erase units of device 1 (MBOOT_DEV1_ERASE, or MBOOT_DEV1_RUNS with the _RUNj_SIZE and _RUNj_ERASE of every run)"
#endif
#if MBOOT_DEV1_RUNS < 1 || MBOOT_DEV1_RUNS > 4
#error "a device has one to four runs of erase units"
#endif
#if !defined(MBOOT_DEV1_RUN0_SIZE) || !defined(MBOOT_DEV1_RUN0_ERASE) \
    || (MBOOT_DEV1_RUNS > 1 && (!defined(MBOOT_DEV1_RUN1_SIZE) || !defined(MBOOT_DEV1_RUN1_ERASE))) \
    || (MBOOT_DEV1_RUNS > 2 && (!defined(MBOOT_DEV1_RUN2_SIZE) || !defined(MBOOT_DEV1_RUN2_ERASE))) \
    || (MBOOT_DEV1_RUNS > 3 && (!defined(MBOOT_DEV1_RUN3_SIZE) || !defined(MBOOT_DEV1_RUN3_ERASE)))
#error "mboot_dev.h does not give the size and the erase unit of every run of device 1"
#endif
// A run behind the last one has no bytes and the erase unit of the run before it.
#if MBOOT_DEV1_RUNS < 2
#define MBOOT_DEV1_RUN1_SIZE 0
#define MBOOT_DEV1_RUN1_ERASE MBOOT_DEV1_RUN0_ERASE
#endif
#if MBOOT_DEV1_RUNS < 3
#define MBOOT_DEV1_RUN2_SIZE 0
#define MBOOT_DEV1_RUN2_ERASE MBOOT_DEV1_RUN1_ERASE
#endif
#if MBOOT_DEV1_RUNS < 4
#define MBOOT_DEV1_RUN3_SIZE 0
#define MBOOT_DEV1_RUN3_ERASE MBOOT_DEV1_RUN2_ERASE
#endif
#define MBOOT_DEV1_RUN0_END (MBOOT_DEV1_RUN0_SIZE)
#define MBOOT_DEV1_RUN1_END (MBOOT_DEV1_RUN0_END + MBOOT_DEV1_RUN1_SIZE)
#define MBOOT_DEV1_RUN2_END (MBOOT_DEV1_RUN1_END + MBOOT_DEV1_RUN2_SIZE)
#define MBOOT_DEV1_RUN3_END (MBOOT_DEV1_RUN2_END + MBOOT_DEV1_RUN3_SIZE)
#if MBOOT_DEV1_RUN3_END != MBOOT_DEV1_SIZE
#error "the runs of device 1 do not add up to its size"
#endif
#if !MBOOT_RUN_OK(0, MBOOT_DEV1_RUN0_SIZE, MBOOT_DEV1_RUN0_ERASE, MBOOT_DEV1_WRITE) \
    || (MBOOT_DEV1_RUNS > 1 && !MBOOT_RUN_OK(MBOOT_DEV1_RUN0_END, MBOOT_DEV1_RUN1_SIZE, MBOOT_DEV1_RUN1_ERASE, MBOOT_DEV1_WRITE)) \
    || (MBOOT_DEV1_RUNS > 2 && !MBOOT_RUN_OK(MBOOT_DEV1_RUN1_END, MBOOT_DEV1_RUN2_SIZE, MBOOT_DEV1_RUN2_ERASE, MBOOT_DEV1_WRITE)) \
    || (MBOOT_DEV1_RUNS > 3 && !MBOOT_RUN_OK(MBOOT_DEV1_RUN2_END, MBOOT_DEV1_RUN3_SIZE, MBOOT_DEV1_RUN3_ERASE, MBOOT_DEV1_WRITE))
#error "every run of device 1 has to hold whole erase units that are a power of two and at least the write unit, and to start at a multiple of its erase unit"
#endif
#if MBOOT_DEV1_RUNS == 1
#define MBOOT_DEV1_ERASE_AT(o) (MBOOT_DEV1_RUN0_ERASE)
#define MBOOT_DEV1_RUN_END_AT(o) (MBOOT_DEV1_RUN0_END)
#else
#define MBOOT_DEV1_ERASE_AT(o) \
    ((o) < MBOOT_DEV1_RUN0_END ? MBOOT_DEV1_RUN0_ERASE : (o) < MBOOT_DEV1_RUN1_END ? MBOOT_DEV1_RUN1_ERASE \
    : (o) < MBOOT_DEV1_RUN2_END ? MBOOT_DEV1_RUN2_ERASE : MBOOT_DEV1_RUN3_ERASE)
#define MBOOT_DEV1_RUN_END_AT(o) \
    ((o) < MBOOT_DEV1_RUN0_END ? MBOOT_DEV1_RUN0_END : (o) < MBOOT_DEV1_RUN1_END ? MBOOT_DEV1_RUN1_END \
    : (o) < MBOOT_DEV1_RUN2_END ? MBOOT_DEV1_RUN2_END : MBOOT_DEV1_RUN3_END)
#endif
#endif

// Lookups by CPU address. The erase unit of an address, the end of the run it lies in, and its
// offset in its device. They stand for the run table that mboot_devs[] holds at run time. An
// offset is an unsigned difference, so that the preprocessor never compares a negative signed
// value with an unsigned run size, which it warns about.
#define MBOOT_OFS(a, base) ((0u + (a)) - (0u + (base)))
#if defined(MBOOT_DEV1_BASE)
#define MBOOT_OFFSET_AT(a) \
    ((a) >= MBOOT_DEV1_BASE ? MBOOT_OFS(a, MBOOT_DEV1_BASE) : MBOOT_OFS(a, MBOOT_DEV0_BASE))
#define MBOOT_UNIT_AT(a) \
    ((a) >= MBOOT_DEV1_BASE ? MBOOT_DEV1_ERASE_AT(MBOOT_OFS(a, MBOOT_DEV1_BASE)) \
    : MBOOT_DEV0_ERASE_AT(MBOOT_OFS(a, MBOOT_DEV0_BASE)))
#define MBOOT_RUN_END_AT(a) \
    ((a) >= MBOOT_DEV1_BASE ? MBOOT_DEV1_BASE + MBOOT_DEV1_RUN_END_AT(MBOOT_OFS(a, MBOOT_DEV1_BASE)) \
    : MBOOT_DEV0_BASE + MBOOT_DEV0_RUN_END_AT(MBOOT_OFS(a, MBOOT_DEV0_BASE)))
#else
#define MBOOT_OFFSET_AT(a) MBOOT_OFS(a, MBOOT_DEV0_BASE)
#define MBOOT_UNIT_AT(a) (MBOOT_DEV0_ERASE_AT(MBOOT_OFS(a, MBOOT_DEV0_BASE)))
#define MBOOT_RUN_END_AT(a) (MBOOT_DEV0_BASE + MBOOT_DEV0_RUN_END_AT(MBOOT_OFS(a, MBOOT_DEV0_BASE)))
#endif

#if MBOOT_POLICY_SINGLE
#define MBOOT_SECONDARY_DEV 0
#elif defined(MBOOT_DEV1_BASE) && MBOOT_SECONDARY_ADDR >= MBOOT_DEV1_BASE
#define MBOOT_SECONDARY_DEV 1
#else
#define MBOOT_SECONDARY_DEV 0
#endif

#if MBOOT_SECONDARY_DEV == 1
#define MBOOT_SECONDARY_WRITE (MBOOT_DEV1_WRITE)
#define MBOOT_SECONDARY_ECC (MBOOT_DEV1_ECC)
#define MBOOT_SECONDARY_MAPPED (MBOOT_DEV1_MAPPED)
#else
#define MBOOT_SECONDARY_WRITE (MBOOT_DEV0_WRITE)
// The single policy has no secondary slot: no second slot with ECC.
#if MBOOT_POLICY_SINGLE
#define MBOOT_SECONDARY_ECC 0
#else
#define MBOOT_SECONDARY_ECC (MBOOT_DEV0_ECC)
#endif
#define MBOOT_SECONDARY_MAPPED (MBOOT_DEV0_MAPPED)
#endif

// bootutil places every trailer field at a multiple of this and writes them in units of the
// write size of the slot device. It is the largest write unit of the two slot devices, at
// least 8.
#if MBOOT_DEV0_WRITE > MBOOT_SECONDARY_WRITE
#define MBOOT_MAX_WRITE_UNIT (MBOOT_DEV0_WRITE)
#else
#define MBOOT_MAX_WRITE_UNIT (MBOOT_SECONDARY_WRITE)
#endif
#if MBOOT_MAX_WRITE_UNIT > 8
#define MCUBOOT_BOOT_MAX_ALIGN MBOOT_MAX_WRITE_UNIT
#define MBOOT_MAX_ALIGN_VALUE MBOOT_MAX_WRITE_UNIT
#else
#define MBOOT_MAX_ALIGN_VALUE 8
#endif
#if MBOOT_MAX_ALIGN_VALUE > 32
#error "the write unit of a slot device is larger than 32 bytes"
#endif
#if (MBOOT_MAX_ALIGN_VALUE % MBOOT_DEV0_WRITE) != 0 || (MBOOT_MAX_ALIGN_VALUE % MBOOT_SECONDARY_WRITE) != 0
#error "the trailer alignment is not a multiple of a slot device write unit"
#endif
// The ECC policy protects each write unit on its own, so a trailer field has to occupy exactly one.
#if MBOOT_DEV0_ECC && MBOOT_DEV0_WRITE != MBOOT_MAX_ALIGN_VALUE
#error "a device with ECC needs a write unit equal to the trailer alignment"
#endif

// ---- slots ----

#ifndef MBOOT_PRIMARY_ADDR
#define MBOOT_PRIMARY_ADDR (MBOOT_DEV0_BASE + MBOOT_BOOT_SIZE)
#endif
#define MBOOT_BOOT_ADDR (MBOOT_DEV0_BASE)

// The erase unit of an area is the one of the run it lies in. The slots, and the scratch and
// shadow areas that work on their sectors, have the unit of the primary slot.
#define MBOOT_BOOT_UNIT MBOOT_UNIT_AT(MBOOT_BOOT_ADDR)
#define MBOOT_PRIMARY_UNIT MBOOT_UNIT_AT(MBOOT_PRIMARY_ADDR)
#define MBOOT_SLOT_UNIT MBOOT_PRIMARY_UNIT

// Size of the secondary slot by mode: swap using offset needs a spare erase unit in front of the
// image, swap using move one in the primary slot for the move, the other modes have two slots of
// equal size.
#if MBOOT_POLICY_SINGLE
#if defined(MBOOT_SECONDARY_ADDR)
#error "MBOOT_POLICY_SEL_SINGLE has one slot, MBOOT_SECONDARY_ADDR must not be defined"
#else
#define MBOOT_SECONDARY_ADDR 0
#endif
#define MBOOT_SECONDARY_SIZE 0
#elif defined(MCUBOOT_SWAP_USING_OFFSET)
#define MBOOT_SECONDARY_SIZE (MBOOT_PRIMARY_SIZE + MBOOT_SLOT_UNIT)
#elif defined(MCUBOOT_SWAP_USING_MOVE)
#define MBOOT_SECONDARY_SIZE (MBOOT_PRIMARY_SIZE - MBOOT_SLOT_UNIT)
#else
#define MBOOT_SECONDARY_SIZE (MBOOT_PRIMARY_SIZE)
#endif
#if !MBOOT_POLICY_SINGLE
#define MBOOT_SECONDARY_UNIT MBOOT_UNIT_AT(MBOOT_SECONDARY_ADDR)
#endif
#define MBOOT_PRIMARY_END (MBOOT_PRIMARY_ADDR + MBOOT_PRIMARY_SIZE)
#define MBOOT_SECONDARY_END (MBOOT_SECONDARY_ADDR + MBOOT_SECONDARY_SIZE)

// bootutil's sector table has an entry for every sector of the larger slot.
#if MBOOT_SECONDARY_SIZE > MBOOT_PRIMARY_SIZE
#define MCUBOOT_MAX_IMG_SECTORS (MBOOT_SECONDARY_SIZE / MBOOT_SLOT_UNIT)
#else
#define MCUBOOT_MAX_IMG_SECTORS (MBOOT_PRIMARY_SIZE / MBOOT_SLOT_UNIT)
#endif

// Trailer of a slot, as bootutil_area.c sizes it. The swap modes keep two states (offset) or
// three (move, scratch) per sector, then the swap info fields and the magic. Overwrite-external
// and single have no status table: the flag words and the magic only (single has one flag word
// more than overwrite-external).
#define MBOOT_ALIGN_UP(x, a) ((((x) + (a) - 1) / (a)) * (a))
#define MBOOT_TRAILER_MAGIC_SIZE MBOOT_ALIGN_UP(16, MBOOT_MAX_ALIGN_VALUE)
#if MBOOT_POLICY_SWAP
#if defined(MCUBOOT_SWAP_USING_OFFSET)
#define MBOOT_TRAILER_SIZE \
    (MCUBOOT_MAX_IMG_SECTORS * 2 * MBOOT_MAX_WRITE_UNIT + MBOOT_MAX_ALIGN_VALUE * 5 \
    + MBOOT_TRAILER_MAGIC_SIZE)
#else
#define MBOOT_TRAILER_SIZE \
    (MCUBOOT_MAX_IMG_SECTORS * 3 * MBOOT_MAX_WRITE_UNIT + MBOOT_MAX_ALIGN_VALUE * 4 \
    + MBOOT_TRAILER_MAGIC_SIZE)
#endif
#define MBOOT_TRAILER_SECTORS ((MBOOT_TRAILER_SIZE + MBOOT_SLOT_UNIT - 1) / MBOOT_SLOT_UNIT)
// Swap using move keeps one sector of the primary slot free for the move.
#if defined(MCUBOOT_SWAP_USING_MOVE)
#define MBOOT_MAX_IMAGE_SIZE (MBOOT_PRIMARY_SIZE - (MBOOT_TRAILER_SECTORS + 1) * MBOOT_SLOT_UNIT)
#else
#define MBOOT_MAX_IMAGE_SIZE (MBOOT_PRIMARY_SIZE - MBOOT_TRAILER_SECTORS * MBOOT_SLOT_UNIT)
#endif
#else
#if MBOOT_POLICY_OVERWRITE_EXTERNAL
#define MBOOT_TRAILER_SIZE (MBOOT_MAX_ALIGN_VALUE * 3 + MBOOT_TRAILER_MAGIC_SIZE)
#else
#define MBOOT_TRAILER_SIZE (MBOOT_MAX_ALIGN_VALUE * 4 + MBOOT_TRAILER_MAGIC_SIZE)
#endif
#define MBOOT_TRAILER_SECTORS ((MBOOT_TRAILER_SIZE + MBOOT_SLOT_UNIT - 1) / MBOOT_SLOT_UNIT)
#define MBOOT_MAX_IMAGE_SIZE (MBOOT_PRIMARY_SIZE - MBOOT_TRAILER_SIZE)
#endif

// The slot an update is written to and what it holds: the secondary slot, or the only slot of
// the single policy. Swap using offset leaves the first erase unit of the secondary slot for the
// image that is replaced.
#if MBOOT_POLICY_SINGLE
#define MBOOT_UPDATE_ADDR (MBOOT_PRIMARY_ADDR)
#define MBOOT_UPDATE_SIZE (MBOOT_PRIMARY_SIZE)
#else
#define MBOOT_UPDATE_ADDR (MBOOT_SECONDARY_ADDR)
#define MBOOT_UPDATE_SIZE (MBOOT_SECONDARY_SIZE)
#endif
#if defined(MCUBOOT_SWAP_USING_OFFSET)
#define MBOOT_UPDATE_SPARE (MBOOT_SLOT_UNIT)
#else
#define MBOOT_UPDATE_SPARE 0
#endif
#define MBOOT_UPDATE_END (MBOOT_UPDATE_ADDR + MBOOT_UPDATE_SIZE)

// The update slot holds an image from after its spare unit up to its trailer sectors. The
// single policy has no swap state: its image may use the whole slot, up to the trailer reservation
// that MBOOT_MAX_IMAGE_SIZE leaves. The application is linked to fit there, and in the primary
// slot.
#if MBOOT_POLICY_SINGLE
#define MBOOT_UPDATE_TRAILER_OFF (MBOOT_UPDATE_SIZE)
#else
#define MBOOT_UPDATE_TRAILER_OFF (MBOOT_UPDATE_SIZE - MBOOT_TRAILER_SECTORS * MBOOT_SLOT_UNIT)
#endif
#define MBOOT_UPDATE_IMAGE_SIZE (MBOOT_UPDATE_TRAILER_OFF - MBOOT_UPDATE_SPARE)
#if MBOOT_UPDATE_IMAGE_SIZE < MBOOT_MAX_IMAGE_SIZE
#define MBOOT_APP_LIMIT (MBOOT_UPDATE_IMAGE_SIZE)
#else
#define MBOOT_APP_LIMIT (MBOOT_MAX_IMAGE_SIZE)
#endif

// Swap using offset only resumes an interrupted swap of an image that, with its header and TLVs,
// is larger than one erase unit: for a smaller one bootutil's boot_read_image_header() reads the
// header of the image under swap from the first unit of the primary slot, which a cut inside the
// erase of that unit leaves neither valid nor erased, and the swap is not resumed. An update of
// at most one erase unit is refused (MBOOT_RES_ERR_TOO_SMALL, or -EINVAL from the application
// writer). The other modes and policies take any image.
#if defined(MCUBOOT_SWAP_USING_OFFSET)
#define MBOOT_MIN_IMAGE_SIZE (MBOOT_SLOT_UNIT + 1)
#else
#define MBOOT_MIN_IMAGE_SIZE 0
#endif

// ---- areas next to the primary slot ----

#if defined(MBOOT_SECCNT_FLASH)
#ifndef MBOOT_SECCNT_ADDR
#define MBOOT_SECCNT_ADDR (MBOOT_PRIMARY_END)
#endif
#define MBOOT_SECCNT_UNIT MBOOT_UNIT_AT(MBOOT_SECCNT_ADDR)
#define MBOOT_SECCNT_SIZE (2 * MBOOT_SECCNT_UNIT)
#define MBOOT_AUX_AFTER_SECCNT (MBOOT_SECCNT_ADDR + MBOOT_SECCNT_SIZE)
#else
#if defined(MBOOT_SECCNT_ADDR)
#error "MBOOT_SECCNT_ADDR needs MBOOT_ROLLBACK_COUNTER 1 with the counter in flash"
#endif
#define MBOOT_AUX_AFTER_SECCNT (MBOOT_PRIMARY_END)
#endif

// One shadow sector for each trailer sector of each slot that is on a device with ECC.
#if MBOOT_DEV0_ECC
#ifndef MBOOT_SHADOW_ADDR
#define MBOOT_SHADOW_ADDR (MBOOT_AUX_AFTER_SECCNT)
#endif
#define MBOOT_ECC_SHADOW 1
#define MBOOT_SHADOW_UNIT MBOOT_UNIT_AT(MBOOT_SHADOW_ADDR)
#define MBOOT_SHADOW_SIZE ((1 + MBOOT_SECONDARY_ECC) * MBOOT_TRAILER_SECTORS * MBOOT_SHADOW_UNIT)
#define MBOOT_AUX_END_SHADOW (MBOOT_SHADOW_ADDR + MBOOT_SHADOW_SIZE)
#else
#if defined(MBOOT_SHADOW_ADDR)
#error "MBOOT_SHADOW_ADDR only applies to a device 0 with ECC"
#endif
#define MBOOT_AUX_END_SHADOW (MBOOT_AUX_AFTER_SECCNT)
#endif

// The scratch area of swap using scratch.
#if defined(MCUBOOT_SWAP_USING_SCRATCH)
// A scratch area that the board places does not move the default of the filesystem.
#ifndef MBOOT_SCRATCH_ADDR
#define MBOOT_SCRATCH_ADDR (MBOOT_AUX_END_SHADOW)
#define MBOOT_SCRATCH_DEFAULT 1
#endif
#define MBOOT_SCRATCH_UNIT MBOOT_UNIT_AT(MBOOT_SCRATCH_ADDR)
#ifndef MBOOT_SCRATCH_SIZE
#define MBOOT_SCRATCH_SIZE (MBOOT_SCRATCH_UNIT)
#endif
#if MBOOT_SCRATCH_SIZE < MBOOT_SCRATCH_UNIT
#error "the scratch area has to be at least one erase unit (the largest sector)"
#endif
#if defined(MBOOT_SCRATCH_DEFAULT)
#define MBOOT_AUX_END_SCRATCH (MBOOT_SCRATCH_ADDR + MBOOT_SCRATCH_SIZE)
#else
#define MBOOT_AUX_END_SCRATCH (MBOOT_AUX_END_SHADOW)
#endif
#else
#if defined(MBOOT_SCRATCH_ADDR) || defined(MBOOT_SCRATCH_SIZE)
#error "MBOOT_SCRATCH_ADDR and MBOOT_SCRATCH_SIZE only apply to MBOOT_SWAP_MODE_SEL_SCRATCH"
#endif
#define MBOOT_AUX_END_SCRATCH (MBOOT_AUX_END_SHADOW)
#endif

// The intent area of the single policy with fsload: the element stream of a request is saved
// there before the only slot is overwritten, so that a power loss during the install repeats it.
#if MBOOT_POLICY_SINGLE && MBOOT_FSLOAD_ENABLE
// An intent area that the board places does not move the default of the filesystem.
#ifndef MBOOT_INTENT_ADDR
#define MBOOT_INTENT_ADDR (MBOOT_AUX_END_SCRATCH)
#define MBOOT_INTENT_DEFAULT 1
#endif
#define MBOOT_INTENT_UNIT MBOOT_UNIT_AT(MBOOT_INTENT_ADDR)
#define MBOOT_INTENT_SIZE (MBOOT_INTENT_UNIT)
#if defined(MBOOT_INTENT_DEFAULT)
#define MBOOT_AUX_END (MBOOT_INTENT_ADDR + MBOOT_INTENT_SIZE)
#else
#define MBOOT_AUX_END (MBOOT_AUX_END_SCRATCH)
#endif
#else
#if defined(MBOOT_INTENT_ADDR)
#error "MBOOT_INTENT_ADDR only applies to MBOOT_POLICY_SEL_SINGLE with fsload"
#endif
#define MBOOT_AUX_END (MBOOT_AUX_END_SCRATCH)
#endif

#if !defined(MBOOT_FS_ADDR)
#define MBOOT_FS_ADDR (MBOOT_AUX_END)
#if MBOOT_SECONDARY_DEV == 0 && !MBOOT_POLICY_SINGLE
#define MBOOT_FS_SIZE (MBOOT_SECONDARY_ADDR - MBOOT_FS_ADDR)
#else
#define MBOOT_FS_SIZE (MBOOT_DEV0_BASE + MBOOT_DEV0_SIZE - MBOOT_FS_ADDR)
#endif
#elif !defined(MBOOT_FS_SIZE)
#error "MBOOT_FS_ADDR needs MBOOT_FS_SIZE"
#endif
#if MBOOT_FS_SIZE <= 0
#error "the filesystem has no room: place it with MBOOT_FS_ADDR and MBOOT_FS_SIZE"
#endif
#define MBOOT_FS_UNIT MBOOT_UNIT_AT(MBOOT_FS_ADDR)

// ---- checks ----

#define MBOOT_OVERLAPS(a, asz, b, bsz) \
    ((asz) > 0 && (bsz) > 0 && (a) < (b) + (bsz) && (b) < (a) + (asz))

#if !defined(MBOOT_PRIMARY_SIZE) || (!MBOOT_POLICY_SINGLE && !defined(MBOOT_SECONDARY_ADDR))
#error "the board has to define MBOOT_PRIMARY_SIZE and MBOOT_SECONDARY_ADDR (MBOOT_SECONDARY_ADDR not with MBOOT_POLICY_SEL_SINGLE)"
#endif
#if !defined(MBOOT_ROLLBACK_COUNTER)
#error "the board has to define MBOOT_ROLLBACK_COUNTER (1 for a security counter, 0 for a version check)"
#endif

// Every area as an address and a size. An area that the configuration does not have has size 0 and
// the address of the start of device 0.
#define MBOOT_CHK_BOOT_ADDR (MBOOT_BOOT_ADDR)
#define MBOOT_CHK_BOOT_SIZE (MBOOT_BOOT_SIZE)
#define MBOOT_CHK_PRIMARY_ADDR (MBOOT_PRIMARY_ADDR)
#define MBOOT_CHK_PRIMARY_SIZE (MBOOT_PRIMARY_SIZE)
#if !MBOOT_POLICY_SINGLE
#define MBOOT_CHK_SECONDARY_ADDR (MBOOT_SECONDARY_ADDR)
#define MBOOT_CHK_SECONDARY_SIZE (MBOOT_SECONDARY_SIZE)
#else
#define MBOOT_CHK_SECONDARY_ADDR (MBOOT_DEV0_BASE)
#define MBOOT_CHK_SECONDARY_SIZE 0
#endif
#if defined(MBOOT_SECCNT_FLASH)
#define MBOOT_CHK_SECCNT_ADDR (MBOOT_SECCNT_ADDR)
#define MBOOT_CHK_SECCNT_SIZE (MBOOT_SECCNT_SIZE)
#else
#define MBOOT_CHK_SECCNT_ADDR (MBOOT_DEV0_BASE)
#define MBOOT_CHK_SECCNT_SIZE 0
#endif
#if defined(MBOOT_ECC_SHADOW)
#define MBOOT_CHK_SHADOW_ADDR (MBOOT_SHADOW_ADDR)
#define MBOOT_CHK_SHADOW_SIZE (MBOOT_SHADOW_SIZE)
#else
#define MBOOT_CHK_SHADOW_ADDR (MBOOT_DEV0_BASE)
#define MBOOT_CHK_SHADOW_SIZE 0
#endif
#if defined(MCUBOOT_SWAP_USING_SCRATCH)
#define MBOOT_CHK_SCRATCH_ADDR (MBOOT_SCRATCH_ADDR)
#define MBOOT_CHK_SCRATCH_SIZE (MBOOT_SCRATCH_SIZE)
#else
#define MBOOT_CHK_SCRATCH_ADDR (MBOOT_DEV0_BASE)
#define MBOOT_CHK_SCRATCH_SIZE 0
#endif
#if defined(MBOOT_INTENT_ADDR)
#define MBOOT_CHK_INTENT_ADDR (MBOOT_INTENT_ADDR)
#define MBOOT_CHK_INTENT_SIZE (MBOOT_INTENT_SIZE)
#else
#define MBOOT_CHK_INTENT_ADDR (MBOOT_DEV0_BASE)
#define MBOOT_CHK_INTENT_SIZE 0
#endif
#define MBOOT_CHK_FS_ADDR (MBOOT_FS_ADDR)
#define MBOOT_CHK_FS_SIZE (MBOOT_FS_SIZE)

// An area lies in one run of erase units, starts at and is a multiple of the erase unit of the run.
#define MBOOT_CHK_UNITS(A) \
    (MBOOT_CHK_##A##_SIZE == 0 \
    || (MBOOT_CHK_##A##_ADDR + MBOOT_CHK_##A##_SIZE <= MBOOT_RUN_END_AT(MBOOT_CHK_##A##_ADDR) \
    && (MBOOT_OFFSET_AT(MBOOT_CHK_##A##_ADDR) % MBOOT_UNIT_AT(MBOOT_CHK_##A##_ADDR)) == 0 \
    && (MBOOT_CHK_##A##_SIZE % MBOOT_UNIT_AT(MBOOT_CHK_##A##_ADDR)) == 0))
// An area inside device 0.
#define MBOOT_CHK_IN_DEV0(A) \
    (MBOOT_CHK_##A##_SIZE == 0 \
    || (MBOOT_CHK_##A##_ADDR >= MBOOT_DEV0_BASE \
    && MBOOT_CHK_##A##_ADDR + MBOOT_CHK_##A##_SIZE <= MBOOT_DEV0_BASE + MBOOT_DEV0_SIZE))
#define MBOOT_CHK_OVERLAP(A, B) \
    MBOOT_OVERLAPS(MBOOT_CHK_##A##_ADDR, MBOOT_CHK_##A##_SIZE, MBOOT_CHK_##B##_ADDR, MBOOT_CHK_##B##_SIZE)

#if !MBOOT_CHK_UNITS(BOOT)
#error "the bootloader region has to lie in one run of erase units, start at and be a multiple of the erase unit"
#endif
#if !MBOOT_CHK_UNITS(PRIMARY)
#error "the primary slot has to lie in one run of erase units, start at and be a multiple of the erase unit"
#endif
#if !MBOOT_CHK_UNITS(SECONDARY)
#error "the secondary slot has to lie in one run of erase units, start at and be a multiple of the erase unit"
#endif
#if !MBOOT_CHK_UNITS(SECCNT)
#error "the counter area has to lie in one run of erase units, start at and be a multiple of the erase unit"
#endif
#if !MBOOT_CHK_UNITS(SHADOW)
#error "the shadow area has to lie in one run of erase units, start at and be a multiple of the erase unit"
#endif
#if !MBOOT_CHK_UNITS(SCRATCH)
#error "the scratch area has to lie in one run of erase units, start at and be a multiple of the erase unit, and be at least one erase unit (the largest sector)"
#endif
#if !MBOOT_CHK_UNITS(INTENT)
#error "the intent area has to lie in one run of erase units and start at a multiple of the erase unit"
#endif
#if !MBOOT_CHK_UNITS(FS)
#error "the filesystem has to lie in one run of erase units, start at and be a multiple of the erase unit"
#endif

#if MBOOT_DEV_OF(MBOOT_PRIMARY_ADDR) != 0
#error "the primary slot has to be in device 0"
#endif
#if !MBOOT_CHK_IN_DEV0(BOOT)
#error "the bootloader region does not fit in device 0"
#endif
#if !MBOOT_CHK_IN_DEV0(PRIMARY)
#error "the primary slot does not fit in device 0"
#endif
#if !MBOOT_CHK_IN_DEV0(SECCNT)
#error "the counter area does not fit in device 0"
#endif
#if !MBOOT_CHK_IN_DEV0(SHADOW)
#error "the shadow area does not fit in device 0"
#endif
#if !MBOOT_CHK_IN_DEV0(SCRATCH)
#error "the scratch area does not fit in device 0"
#endif
#if !MBOOT_CHK_IN_DEV0(INTENT)
#error "the intent area does not fit in device 0"
#endif
#if !MBOOT_POLICY_SINGLE
#if MBOOT_SECONDARY_DEV == 0
#if MBOOT_SECONDARY_END > MBOOT_DEV0_BASE + MBOOT_DEV0_SIZE
#error "the secondary slot does not fit in device 0"
#endif
#else
#if MBOOT_SECONDARY_END > MBOOT_DEV1_BASE + MBOOT_DEV1_SIZE
#error "the secondary slot does not fit in device 1"
#endif
#endif
#if MBOOT_SECONDARY_UNIT != MBOOT_PRIMARY_UNIT
#error "the erase unit of the secondary slot device differs from the one of the primary slot"
#endif
#endif
#if MBOOT_DEV_OF(MBOOT_FS_ADDR) == 0
#if MBOOT_FS_ADDR + MBOOT_FS_SIZE > MBOOT_DEV0_BASE + MBOOT_DEV0_SIZE
#error "the filesystem does not fit in device 0"
#endif
#elif MBOOT_FS_ADDR + MBOOT_FS_SIZE > MBOOT_DEV1_BASE + MBOOT_DEV1_SIZE
#error "the filesystem does not fit in device 1"
#endif

#if defined(MBOOT_ECC_SHADOW) && MBOOT_SHADOW_UNIT != MBOOT_SLOT_UNIT
#error "the shadow area needs the erase unit of the slots: it holds one sector for every sector of their trailers"
#endif
// The scratch area takes the sectors of a slot one at a time.
#if defined(MCUBOOT_SWAP_USING_SCRATCH) && MBOOT_SCRATCH_UNIT != MBOOT_SLOT_UNIT
#error "the scratch area needs the erase unit of the slots"
#endif

// No two areas overlap. The filesystem on device 1 and the secondary slot there are told apart
// by their addresses.
#if MBOOT_CHK_OVERLAP(BOOT, PRIMARY)
#error "the bootloader region overlaps the primary slot"
#elif MBOOT_CHK_OVERLAP(BOOT, SECONDARY)
#error "the bootloader region overlaps the secondary slot"
#elif MBOOT_CHK_OVERLAP(BOOT, SECCNT)
#error "the bootloader region overlaps the counter area"
#elif MBOOT_CHK_OVERLAP(BOOT, SHADOW)
#error "the bootloader region overlaps the shadow area"
#elif MBOOT_CHK_OVERLAP(BOOT, SCRATCH)
#error "the bootloader region overlaps the scratch area"
#elif MBOOT_CHK_OVERLAP(BOOT, INTENT)
#error "the bootloader region overlaps the intent area"
#elif MBOOT_CHK_OVERLAP(BOOT, FS)
#error "the bootloader region overlaps the filesystem"
#elif MBOOT_CHK_OVERLAP(PRIMARY, SECONDARY)
#error "the primary slot overlaps the secondary slot"
#elif MBOOT_CHK_OVERLAP(PRIMARY, SECCNT)
#error "the primary slot overlaps the counter area"
#elif MBOOT_CHK_OVERLAP(PRIMARY, SHADOW)
#error "the primary slot overlaps the shadow area"
#elif MBOOT_CHK_OVERLAP(PRIMARY, SCRATCH)
#error "the primary slot overlaps the scratch area"
#elif MBOOT_CHK_OVERLAP(PRIMARY, INTENT)
#error "the primary slot overlaps the intent area"
#elif MBOOT_CHK_OVERLAP(PRIMARY, FS)
#error "the filesystem overlaps the primary slot"
#elif MBOOT_CHK_OVERLAP(SECONDARY, SECCNT)
#error "the secondary slot overlaps the counter area"
#elif MBOOT_CHK_OVERLAP(SECONDARY, SHADOW)
#error "the secondary slot overlaps the shadow area"
#elif MBOOT_CHK_OVERLAP(SECONDARY, SCRATCH)
#error "the secondary slot overlaps the scratch area"
#elif MBOOT_CHK_OVERLAP(SECONDARY, INTENT)
#error "the secondary slot overlaps the intent area"
#elif MBOOT_CHK_OVERLAP(SECONDARY, FS)
#error "the filesystem overlaps the secondary slot"
#elif MBOOT_CHK_OVERLAP(SECCNT, SHADOW)
#error "the counter area overlaps the shadow area"
#elif MBOOT_CHK_OVERLAP(SECCNT, SCRATCH)
#error "the counter area overlaps the scratch area"
#elif MBOOT_CHK_OVERLAP(SECCNT, INTENT)
#error "the counter area overlaps the intent area"
#elif MBOOT_CHK_OVERLAP(SECCNT, FS)
#error "the filesystem overlaps the counter area"
#elif MBOOT_CHK_OVERLAP(SHADOW, SCRATCH)
#error "the shadow area overlaps the scratch area"
#elif MBOOT_CHK_OVERLAP(SHADOW, INTENT)
#error "the shadow area overlaps the intent area"
#elif MBOOT_CHK_OVERLAP(SHADOW, FS)
#error "the filesystem overlaps the shadow area"
#elif MBOOT_CHK_OVERLAP(SCRATCH, INTENT)
#error "the scratch area overlaps the intent area"
#elif MBOOT_CHK_OVERLAP(SCRATCH, FS)
#error "the scratch area overlaps the filesystem"
#elif MBOOT_CHK_OVERLAP(INTENT, FS)
#error "the intent area overlaps the filesystem"
#endif

#if defined(MCUBOOT_SWAP_USING_MOVE) && MBOOT_PRIMARY_SIZE < 2 * MBOOT_SLOT_UNIT
#error "swap using move needs a primary slot of at least two erase units"
#endif
// The shadow words of the ECC policy cover the trailers of the two slots only; the swap status
// that scratch swap keeps in the scratch area would not survive a torn flash word.
#if defined(MCUBOOT_SWAP_USING_SCRATCH) && MBOOT_DEV0_ECC
#error "swap using scratch is not supported on a device with ECC: the swap status in the scratch area is not covered by the shadow area"
#endif

#if (MBOOT_HEADER_SIZE % MBOOT_VTOR_ALIGN) != 0 || (MBOOT_HEADER_SIZE % MBOOT_MAX_ALIGN_VALUE) != 0 \
    || MBOOT_HEADER_SIZE < 0x20 || (MBOOT_PRIMARY_ADDR % MBOOT_VTOR_ALIGN) != 0
#error "the image header size and the primary slot address have to be multiples of the vector table alignment"
#endif
#if MBOOT_APP_LIMIT <= MBOOT_HEADER_SIZE + MBOOT_TLV_RESERVE
#error "the header and the TLV reserve leave no room in the maximum image size"
#endif
#if MBOOT_UPDATE_IMAGE_SIZE <= MBOOT_HEADER_SIZE + MBOOT_TLV_RESERVE
#error "the update slot leaves no room for an image before its trailer"
#endif
#if (MBOOT_TMPBUF_SZ % MBOOT_MAX_ALIGN_VALUE) != 0
#error "MBOOT_TMPBUF_SZ has to be a multiple of the trailer alignment"
#endif
#if MCUBOOT_MAX_IMG_SECTORS > 4096
#error "the slots have more than 4096 sectors"
#endif
#if MBOOT_DEV0_ECC && !MBOOT_DFU_ENABLE
#error "a device with ECC needs the DFU front end, it is the way out of a torn flash word"
#endif
#if MBOOT_LOG_LEVEL < 0 || MBOOT_LOG_LEVEL > 4
#error "MBOOT_LOG_LEVEL is 0 to 4"
#endif
#if MBOOT_SECURITY_COUNTER < 0 || MBOOT_SECURITY_COUNTER > 0xFFFFFFFF
#error "MBOOT_SECURITY_COUNTER does not fit in 32 bits"
#endif
#if MBOOT_DFU_ENABLE && (MBOOT_DFU_VID < 1 || MBOOT_DFU_VID > 0xFFFF || MBOOT_DFU_PID < 1 || MBOOT_DFU_PID > 0xFFFF)
#error "MBOOT_DFU_VID and MBOOT_DFU_PID are 16 bit USB ids"
#endif
#if MBOOT_HASH_DIRECT
#if !MBOOT_DEV0_MAPPED || !MBOOT_SECONDARY_MAPPED
#error "MBOOT_HASH_DIRECT needs memory mapped slot devices (hashing reads the flash in place)"
#endif
// bootutil_img_hash() with MCUBOOT_HASH_STORAGE_DIRECTLY hashes from the start of the area and
// leaves out the erase unit that swap using offset puts in front of the image in the secondary
// slot, so a valid secondary image would fail validation.
#if defined(MCUBOOT_SWAP_USING_OFFSET)
#error "MBOOT_HASH_DIRECT is not supported with swap using offset: direct hashing ignores the erase unit in front of the image in the secondary slot"
#endif
// The fsload stream area has no flash at the device base to read in place.
#if MBOOT_FSLOAD_ENABLE
#error "MBOOT_HASH_DIRECT cannot hash the fsload stream area: not with fsload"
#endif
#endif

// The primary slot, its trailer and everything else the bootloader trusts for a decision stay on
// device 0, which runs the application. Device 1 only holds data that is validated before use:
// the secondary slot (by signature) and the filesystem (read only). The shadow area of the ECC
// policy covers device 0 only.
#if defined(MBOOT_DEV1_BASE)
#if MBOOT_DEV1_ECC
#error "device 1 has no ECC in this configuration"
#endif
// Device 1 is a SPI flash behind drivers/memory/spiflash.c, which erases blocks of 4 KiB.
#if (MBOOT_DEV1_RUN0_ERASE % 4096) != 0 || (MBOOT_DEV1_RUNS > 1 && (MBOOT_DEV1_RUN1_ERASE % 4096) != 0) \
    || (MBOOT_DEV1_RUNS > 2 && (MBOOT_DEV1_RUN2_ERASE % 4096) != 0) \
    || (MBOOT_DEV1_RUNS > 3 && (MBOOT_DEV1_RUN3_ERASE % 4096) != 0)
#error "the erase unit of device 1 has to be a multiple of the 4 KiB erase block of a SPI flash"
#endif
// Trailer fields of a secondary slot on SPI flash are one write unit each. The SPI NOR tests only
// cover that case.
#if MBOOT_SECONDARY_DEV == 1 && MBOOT_DEV1_WRITE != MBOOT_MAX_ALIGN_VALUE
#error "the write unit of a secondary slot on device 1 has to equal the trailer alignment"
#endif
#endif

// ---- identity ----

// Binds a signed image to the flash layout it was linked for (custom TLV of the image):
// FNV-1 over the 32 bit words below. The default swap-using-offset configuration uses the first
// ten words; other policies, swap modes, counter backends or version checks, and counter or shadow
// areas placed away from their defaults add more. tools/mboot_gen.py reads the same expression.
#define MBOOT_ID_STEP(h, w) ((((h) ^ ((w) & 0xFFFFFFFFu)) * 16777619u) & 0xFFFFFFFFu)
#define MBOOT_ID_0 0x811C9DC5u
#define MBOOT_ID_1 MBOOT_ID_STEP(MBOOT_ID_0, MBOOT_PRIMARY_ADDR)
#define MBOOT_ID_2 MBOOT_ID_STEP(MBOOT_ID_1, MBOOT_PRIMARY_SIZE)
#define MBOOT_ID_3 MBOOT_ID_STEP(MBOOT_ID_2, MBOOT_SECONDARY_ADDR)
#define MBOOT_ID_4 MBOOT_ID_STEP(MBOOT_ID_3, MBOOT_SECONDARY_SIZE)
#define MBOOT_ID_5 MBOOT_ID_STEP(MBOOT_ID_4, MBOOT_FS_ADDR)
#define MBOOT_ID_6 MBOOT_ID_STEP(MBOOT_ID_5, MBOOT_FS_SIZE)
#define MBOOT_ID_7 MBOOT_ID_STEP(MBOOT_ID_6, MBOOT_HEADER_SIZE)
#define MBOOT_ID_8 MBOOT_ID_STEP(MBOOT_ID_7, MBOOT_SLOT_UNIT)
#define MBOOT_ID_9 MBOOT_ID_STEP(MBOOT_ID_8, MBOOT_MAX_WRITE_UNIT)
#define MBOOT_ID_10 MBOOT_ID_STEP(MBOOT_ID_9, MBOOT_ROLLBACK_COUNTER)

// Bit 0..2: policy and swap mode (0 swap using offset, 1 move, 2 scratch, 3 overwrite-external,
// 4 single), bit 3: the counter is kept by the port, bit 4: no version check.
#if MBOOT_POLICY_SINGLE
#define MBOOT_ID_MODE 4
#elif MBOOT_POLICY_OVERWRITE_EXTERNAL
#define MBOOT_ID_MODE 3
#elif defined(MCUBOOT_SWAP_USING_SCRATCH)
#define MBOOT_ID_MODE 2
#elif defined(MCUBOOT_SWAP_USING_MOVE)
#define MBOOT_ID_MODE 1
#else
#define MBOOT_ID_MODE 0
#endif
#if defined(MBOOT_SECCNT_PORT)
#define MBOOT_ID_COUNTER 8
#else
#define MBOOT_ID_COUNTER 0
#endif
#if !MBOOT_ROLLBACK_COUNTER && !MBOOT_VERSION_CHECK
#define MBOOT_ID_VERSION 16
#else
#define MBOOT_ID_VERSION 0
#endif
#if defined(MCUBOOT_SWAP_USING_SCRATCH)
#define MBOOT_ID_SCRATCH_ADDR MBOOT_SCRATCH_ADDR
#define MBOOT_ID_SCRATCH_SIZE MBOOT_SCRATCH_SIZE
#else
#define MBOOT_ID_SCRATCH_ADDR 0
#define MBOOT_ID_SCRATCH_SIZE 0
#endif
#if defined(MBOOT_INTENT_ADDR)
#define MBOOT_ID_INTENT_ADDR MBOOT_INTENT_ADDR
#else
#define MBOOT_ID_INTENT_ADDR 0
#endif
#if MBOOT_ID_MODE == 0 && MBOOT_ID_COUNTER == 0 && MBOOT_ID_VERSION == 0
#define MBOOT_ID_11 MBOOT_ID_10
#else
#define MBOOT_ID_11 MBOOT_ID_STEP(MBOOT_ID_STEP(MBOOT_ID_STEP(MBOOT_ID_STEP( \
    MBOOT_ID_10, MBOOT_ID_MODE + MBOOT_ID_COUNTER + MBOOT_ID_VERSION), \
    MBOOT_ID_SCRATCH_ADDR), MBOOT_ID_SCRATCH_SIZE), MBOOT_ID_INTENT_ADDR)
#endif
// The counter and shadow areas, when a board places them away from their defaults.
#if defined(MBOOT_SECCNT_FLASH) && MBOOT_SECCNT_ADDR != MBOOT_PRIMARY_END
#define MBOOT_ID_SECCNT_ADDR MBOOT_SECCNT_ADDR
#else
#define MBOOT_ID_SECCNT_ADDR 0
#endif
#if defined(MBOOT_ECC_SHADOW) && MBOOT_SHADOW_ADDR != MBOOT_AUX_AFTER_SECCNT
#define MBOOT_ID_SHADOW_ADDR MBOOT_SHADOW_ADDR
#else
#define MBOOT_ID_SHADOW_ADDR 0
#endif
#if MBOOT_ID_SECCNT_ADDR == 0 && MBOOT_ID_SHADOW_ADDR == 0
#define MBOOT_ID_12 MBOOT_ID_11
#else
#define MBOOT_ID_12 MBOOT_ID_STEP(MBOOT_ID_STEP(MBOOT_ID_11, MBOOT_ID_SECCNT_ADDR), MBOOT_ID_SHADOW_ADDR)
#endif
#define MBOOT_LAYOUT_ID MBOOT_ID_STEP(MBOOT_ID_12, MBOOT_API_VERSION)

#endif // MICROPY_INCLUDED_SHARED_MBOOT_MBOOT_LAYOUT_H
