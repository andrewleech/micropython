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

#ifndef MICROPY_INCLUDED_SHARED_MCUBOOT_MCUBOOT_LAYOUT_H
#define MICROPY_INCLUDED_SHARED_MCUBOOT_MCUBOOT_LAYOUT_H

// The MCUboot configuration of a build, derived from two headers found through the include path:
//
//   mpconfigboard.h   the board: slot placement and the choices below
//   mcuboot_dev.h     the port: geometry of the flash devices (MCUBOOT_DEV0_*, MCUBOOT_DEV1_*)
//
// This header only holds numbers and macros, so tools/mcuboot_gen.py can run it through the C
// preprocessor to get the same values for the linker scripts, the image signing and the
// Makefiles. All addresses are CPU addresses (DFU addresses for a device that is not memory
// mapped).
//
// Devices (mcuboot_dev.h). Device 0 is the internal flash and holds the bootloader, device 1 is
// optional (a SPI flash). Each has MCUBOOT_DEVn_NAME, _BASE, _SIZE, _WRITE (program unit, at most
// 32 bytes), _ERASED_VAL, _MAPPED and _ECC, and gives its erase units one of two ways:
//   MCUBOOT_DEVn_ERASE          equal erase units over the whole device
//   MCUBOOT_DEVn_RUNS           k runs (1 to 4) of equal erase units, one behind the other from the
//                               device base: MCUBOOT_DEVn_RUNj_SIZE and MCUBOOT_DEVn_RUNj_ERASE for
//                               j below k. The sizes add up to MCUBOOT_DEVn_SIZE, and a run starts
//                               at, and is a multiple of, its own erase unit.
// The port also gives MCUBOOT_BOOT_SIZE (the bootloader region at the start of device 0, unless the
// board defines it first), MCUBOOT_VTOR_ALIGN and the RAM symbols.
//
// Erase units belong to areas. Every area (the bootloader region, the slots, the counter, the
// shadow, scratch and intent areas and the filesystem) lies in one run of its device, starts at
// and is a multiple of the erase unit of that run (MCUBOOT_<AREA>_UNIT), and overlaps no other
// area. The primary slot, the secondary slot and the scratch and shadow areas have one erase
// unit, because bootutil exchanges the slots sector by sector.
//
// Board inputs. Sizes are in bytes, a size of an area is a multiple of its erase unit.
//   MCUBOOT_PRIMARY_SIZE        required, slot the CPU boots from
//   MCUBOOT_SECONDARY_ADDR      required except for MCUBOOT_POLICY_SEL_SINGLE (not allowed there), update
//                               slot. Its size follows from the policy and the swap mode: one erase
//                               unit larger than the primary slot for swap using offset, one erase
//                               unit smaller for swap using move, as large otherwise
//   MCUBOOT_ROLLBACK_COUNTER    required, 1 keeps a monotonic security counter (in flash, or kept by
//                               the port with MCUBOOT_SECCNT_PORT; use this on a product), 0 only
//                               refuses an older version
//   MCUBOOT_PRIMARY_ADDR        default: directly after the bootloader region
//   MCUBOOT_BOOT_SIZE           bootloader region at the start of device 0 (default of the port); it
//                               lies in one run and is a number of its erase units
//   MCUBOOT_HEADER_SIZE         image header (default 0x400)
//   MCUBOOT_SECURITY_COUNTER    value signed into the image (default 0)
//   MCUBOOT_SECCNT_ADDR         security counter in flash, two erase units (default: after the primary slot)
//   MCUBOOT_SHADOW_ADDR         shadow words of a device 0 with ECC (default: after the counter)
//   MCUBOOT_FS_ADDR, _SIZE      the application filesystem, default the flash between the
//                               auxiliary areas and the secondary slot
//   MCUBOOT_DFU                 0 or 1, DFU front end (default 1)
//   MCUBOOT_DFU_VID, _PID       USB ids of the bootloader (default MICROPY_HW_USB_VID or 0xF055, and 0xDFA5)
//   MCUBOOT_DFU_PRODUCT         USB product string (default board name + " MCUboot")
//   MCUBOOT_DFU_TIMEOUT_S       inactivity timeout of a requested recovery, 0 for none (default 120)
//   MCUBOOT_LOG_LEVEL           0 off to 4 debug (default 3)
//   MCUBOOT_TMPBUF_SZ           hash read chunk (default 4096)
//
// Choices (defaults in brackets). The alternatives of MCUBOOT_POLICY, MCUBOOT_SWAP_MODE and
// MCUBOOT_CRYPTO are the constants of the same prefix with _SEL_ in the name, which this header
// defines before it reads the board.
//   MCUBOOT_POLICY              MCUBOOT_POLICY_SEL_SWAP: two slots, a new image is exchanged with
//                                 the running one and reverts unless it is confirmed (default)
//                               MCUBOOT_POLICY_SEL_OVERWRITE_EXTERNAL: two slots of equal size, a new
//                                 image is copied over the primary slot; no revert; the secondary slot
//                                 may be on device 1
//                               MCUBOOT_POLICY_SEL_SINGLE: one slot that is overwritten in place; no
//                                 revert; needs MCUBOOT_ROLLBACK_COUNTER 1; with fsload an intent area.
//                                 The slot holds no swap state, so an image may fill it up to the
//                                 trailer reservation of MCUBOOT_TRAILER_SIZE bytes at its end
//   MCUBOOT_SWAP_MODE           with the swap policy: MCUBOOT_SWAP_MODE_SEL_OFFSET (default),
//                                 MCUBOOT_SWAP_MODE_SEL_MOVE or MCUBOOT_SWAP_MODE_SEL_SCRATCH
//   MCUBOOT_SCRATCH_SIZE        scratch area of swap using scratch, a multiple of its erase unit
//                               (default one erase unit)
//   MCUBOOT_SCRATCH_ADDR, MCUBOOT_INTENT_ADDR
//                               placement of the scratch area and of the intent area (of the
//                               single policy with fsload; one erase unit), on device 0
//   MCUBOOT_CRYPTO              MCUBOOT_CRYPTO_SEL_TINYCRYPT (default) or MCUBOOT_CRYPTO_SEL_MBEDTLS
//                               (the port provides the mbedtls sources and configuration)
//   MCUBOOT_FIH_LEVEL           fault injection hardening: 0 off, 1 low, 2 medium, 3 high; 3 needs
//                               mcuboot_port_entropy_u8() (default 0)
//   MCUBOOT_SECCNT_PORT         1 keeps the security counter in the port (mcuboot_port_seccnt_*)
//                               instead of in flash; needs MCUBOOT_ROLLBACK_COUNTER 1
//   MCUBOOT_VERSION_CHECK       with MCUBOOT_ROLLBACK_COUNTER 0: 0 disables the refusal of an older
//                               version, so that there is no rollback protection at all (default 1)
//   MCUBOOT_CONFIRM_AUTO        1 confirms a new image after boot.py has run (default 0)
//   MCUBOOT_HASH_DIRECT         1 hashes an image in place instead of through a read buffer; needs
//                               memory mapped slot devices, not with swap using offset or fsload
//   MCUBOOT_VALIDATE_PRIMARY    0 starts the primary slot without a signature check, so that an
//                               application that can write that slot bypasses the signature; refused
//                               with the single policy and with MCUBOOT_PRODUCTION=1 (default 1)
//   MCUBOOT_FSLOAD_FAT, _LFS2, _RAW
//                               define as 1 to read an update from a FAT or littlefs2 filesystem, or
//                               from a raw window of flash
//   MCUBOOT_FSLOAD_GZIP         define as 1 to accept a gzip compressed update file
//
// Placement. The counter, shadow, scratch and intent areas follow the primary slot in that order,
// each one directly behind the one before unless the board names its address, and are on device 0.
// The default of the filesystem is behind them, except that a scratch or intent area that the
// board places leaves it where it would be without. A secondary slot or filesystem on device 1 (a SPI flash) is
// allowed, the other areas are not. Device 1 has no ECC and its erase units are a multiple of the
// 4 KiB erase block; a secondary slot on it needs the erase unit of the primary slot and a write
// unit equal to the trailer alignment.

#define MCUBOOT_API_VERSION 1u

// Alternatives of the choices above, defined before the board is read so that mpconfigboard.h can
// name them.
#define MCUBOOT_POLICY_SEL_SWAP 1
#define MCUBOOT_POLICY_SEL_OVERWRITE_EXTERNAL 2
#define MCUBOOT_POLICY_SEL_SINGLE 3
#define MCUBOOT_SWAP_MODE_SEL_OFFSET 1
#define MCUBOOT_SWAP_MODE_SEL_MOVE 2
#define MCUBOOT_SWAP_MODE_SEL_SCRATCH 3
#define MCUBOOT_CRYPTO_SEL_TINYCRYPT 1
#define MCUBOOT_CRYPTO_SEL_MBEDTLS 2

#include "mpconfigboard.h"
#include "mcuboot_dev.h"

// ---- inputs ----

#ifndef MCUBOOT_HEADER_SIZE
#define MCUBOOT_HEADER_SIZE 0x400u
#endif
#ifndef MCUBOOT_TLV_RESERVE
#define MCUBOOT_TLV_RESERVE 0x400u
#endif
#ifndef MCUBOOT_SECURITY_COUNTER
#define MCUBOOT_SECURITY_COUNTER 0
#endif
#ifndef MCUBOOT_DFU
#define MCUBOOT_DFU 1
#endif
#ifndef MCUBOOT_DFU_VID
#if defined(MICROPY_HW_USB_VID)
#define MCUBOOT_DFU_VID MICROPY_HW_USB_VID
#else
#define MCUBOOT_DFU_VID 0xF055
#endif
#endif
#ifndef MCUBOOT_DFU_PID
#define MCUBOOT_DFU_PID 0xDFA5
#endif
#ifndef MCUBOOT_DFU_PRODUCT
#if defined(MICROPY_HW_BOARD_NAME)
#define MCUBOOT_DFU_PRODUCT MICROPY_HW_BOARD_NAME " MCUboot"
#else
#define MCUBOOT_DFU_PRODUCT "MicroPython MCUboot"
#endif
#endif
#ifndef MCUBOOT_DFU_TIMEOUT_S
#define MCUBOOT_DFU_TIMEOUT_S 120
#endif
#ifndef MCUBOOT_LOG_LEVEL
#define MCUBOOT_LOG_LEVEL 3
#endif
#ifndef MCUBOOT_TMPBUF_SZ
#define MCUBOOT_TMPBUF_SZ 4096
#endif

#define MCUBOOT_DFU_ENABLE (MCUBOOT_DFU)

// ---- feature selection ----

// A feature macro that a board defines to 0 is the same as one that it does not define: below,
// a feature is on when its macro is defined (to 1).
#if defined(MCUBOOT_FSLOAD_FAT) && !(MCUBOOT_FSLOAD_FAT)
#undef MCUBOOT_FSLOAD_FAT
#endif
#if defined(MCUBOOT_FSLOAD_LFS2) && !(MCUBOOT_FSLOAD_LFS2)
#undef MCUBOOT_FSLOAD_LFS2
#endif
#if defined(MCUBOOT_FSLOAD_RAW) && !(MCUBOOT_FSLOAD_RAW)
#undef MCUBOOT_FSLOAD_RAW
#endif
#if defined(MCUBOOT_FSLOAD_GZIP) && !(MCUBOOT_FSLOAD_GZIP)
#undef MCUBOOT_FSLOAD_GZIP
#endif
#if defined(MCUBOOT_CONFIRM_AUTO) && !(MCUBOOT_CONFIRM_AUTO)
#undef MCUBOOT_CONFIRM_AUTO
#endif
#if defined(MCUBOOT_SECCNT_PORT) && !(MCUBOOT_SECCNT_PORT)
#undef MCUBOOT_SECCNT_PORT
#endif

// Slot policy. MCUBOOT_POLICY_SWAP, _OVERWRITE_EXTERNAL and _SINGLE are always defined, to 0 or 1.
#ifndef MCUBOOT_POLICY
#define MCUBOOT_POLICY (MCUBOOT_POLICY_SEL_SWAP)
#endif
#if MCUBOOT_POLICY == MCUBOOT_POLICY_SEL_SWAP
#define MCUBOOT_POLICY_SWAP 1
#define MCUBOOT_POLICY_OVERWRITE_EXTERNAL 0
#define MCUBOOT_POLICY_SINGLE 0
#elif MCUBOOT_POLICY == MCUBOOT_POLICY_SEL_OVERWRITE_EXTERNAL
#define MCUBOOT_POLICY_SWAP 0
#define MCUBOOT_POLICY_OVERWRITE_EXTERNAL 1
#define MCUBOOT_POLICY_SINGLE 0
#elif MCUBOOT_POLICY == MCUBOOT_POLICY_SEL_SINGLE
#define MCUBOOT_POLICY_SWAP 0
#define MCUBOOT_POLICY_OVERWRITE_EXTERNAL 0
#define MCUBOOT_POLICY_SINGLE 1
#else
#error "MCUBOOT_POLICY is MCUBOOT_POLICY_SEL_SWAP, MCUBOOT_POLICY_SEL_OVERWRITE_EXTERNAL or MCUBOOT_POLICY_SEL_SINGLE"
#endif

// The algorithm that moves an update into place.
#if MCUBOOT_POLICY_SWAP
#ifndef MCUBOOT_SWAP_MODE
#define MCUBOOT_SWAP_MODE (MCUBOOT_SWAP_MODE_SEL_OFFSET)
#endif
#if MCUBOOT_SWAP_MODE == MCUBOOT_SWAP_MODE_SEL_OFFSET
#define MCUBOOT_SWAP_USING_OFFSET 1
// Lets an install into the secondary slot revive a device whose primary slot header is erased.
#define MCUBOOT_BOOTSTRAP 1
#elif MCUBOOT_SWAP_MODE == MCUBOOT_SWAP_MODE_SEL_MOVE
#define MCUBOOT_SWAP_USING_MOVE 1
#elif MCUBOOT_SWAP_MODE == MCUBOOT_SWAP_MODE_SEL_SCRATCH
#define MCUBOOT_SWAP_USING_SCRATCH 1
#else
#error "MCUBOOT_SWAP_MODE is MCUBOOT_SWAP_MODE_SEL_OFFSET, MCUBOOT_SWAP_MODE_SEL_MOVE or MCUBOOT_SWAP_MODE_SEL_SCRATCH"
#endif
#else
#if defined(MCUBOOT_SWAP_MODE)
#error "MCUBOOT_SWAP_MODE only applies to MCUBOOT_POLICY_SEL_SWAP"
#endif
#if MCUBOOT_POLICY_OVERWRITE_EXTERNAL
#define MCUBOOT_OVERWRITE_ONLY 1
#define MCUBOOT_OVERWRITE_ONLY_FAST 1
#else
#define MCUBOOT_SINGLE_APPLICATION_SLOT 1
#endif
#endif

// Signature and hash backend. The signature is always ECDSA P-256.
#ifndef MCUBOOT_CRYPTO
#define MCUBOOT_CRYPTO (MCUBOOT_CRYPTO_SEL_TINYCRYPT)
#endif
#if MCUBOOT_CRYPTO == MCUBOOT_CRYPTO_SEL_TINYCRYPT
#define MCUBOOT_USE_TINYCRYPT 1
#elif MCUBOOT_CRYPTO == MCUBOOT_CRYPTO_SEL_MBEDTLS
#define MCUBOOT_USE_MBED_TLS 1
#else
#error "MCUBOOT_CRYPTO is MCUBOOT_CRYPTO_SEL_TINYCRYPT or MCUBOOT_CRYPTO_SEL_MBEDTLS"
#endif

// Fault injection hardening.
#ifndef MCUBOOT_FIH_LEVEL
#define MCUBOOT_FIH_LEVEL 0
#endif
#if MCUBOOT_FIH_LEVEL == 0
#define MCUBOOT_FIH_PROFILE_OFF 1
#elif MCUBOOT_FIH_LEVEL == 1
#define MCUBOOT_FIH_PROFILE_LOW 1
#elif MCUBOOT_FIH_LEVEL == 2
#define MCUBOOT_FIH_PROFILE_MEDIUM 1
#elif MCUBOOT_FIH_LEVEL == 3
#define MCUBOOT_FIH_PROFILE_HIGH 1
#else
#error "MCUBOOT_FIH_LEVEL is 0 (off), 1 (low), 2 (medium) or 3 (high)"
#endif

// Rollback protection.
#if MCUBOOT_ROLLBACK_COUNTER
#if defined(MCUBOOT_VERSION_CHECK)
#error "MCUBOOT_VERSION_CHECK only applies to MCUBOOT_ROLLBACK_COUNTER 0"
#endif
#define MCUBOOT_HW_ROLLBACK_PROT 1
#if !defined(MCUBOOT_SECCNT_PORT)
#define MCUBOOT_SECCNT_FLASH 1
#endif
#else
#if defined(MCUBOOT_SECCNT_PORT)
#error "MCUBOOT_SECCNT_PORT needs MCUBOOT_ROLLBACK_COUNTER 1"
#endif
#if MCUBOOT_POLICY_SINGLE
#error "MCUBOOT_POLICY_SEL_SINGLE needs MCUBOOT_ROLLBACK_COUNTER 1: a version comparison with the primary slot cannot run after the only slot has been overwritten"
#endif
#ifndef MCUBOOT_VERSION_CHECK
#define MCUBOOT_VERSION_CHECK 1
#endif
#if MCUBOOT_VERSION_CHECK
#define MCUBOOT_DOWNGRADE_PREVENTION 1
#define MCUBOOT_DOWNGRADE_PREVENTION_SECURITY_COUNTER 0
#endif
#endif

// Hash the image in place.
#ifndef MCUBOOT_HASH_DIRECT
#define MCUBOOT_HASH_DIRECT 0
#endif
#if MCUBOOT_HASH_DIRECT
#define MCUBOOT_HASH_STORAGE_DIRECTLY 1
#endif

// Validation of the primary slot at every boot. Without it the bootloader starts whatever the
// primary slot holds.
#ifndef MCUBOOT_VALIDATE_PRIMARY
#define MCUBOOT_VALIDATE_PRIMARY 1
#endif
#if MCUBOOT_VALIDATE_PRIMARY
#define MCUBOOT_VALIDATE_PRIMARY_SLOT 1
#elif MCUBOOT_POLICY_SINGLE
#error "MCUBOOT_VALIDATE_PRIMARY 0 is not allowed with MCUBOOT_POLICY_SEL_SINGLE: the only slot is rewritten by DFU and fsload and has to be validated at every boot"
#elif defined(MCUBOOT_PRODUCTION) && MCUBOOT_PRODUCTION
#error "MCUBOOT_VALIDATE_PRIMARY 0 is refused in a production build (MCUBOOT_PRODUCTION=1): the primary slot would start without a signature check"
#else
#warning "MCUBOOT_VALIDATE_PRIMARY is 0: the primary slot starts without a signature check, an application that can write it bypasses secure boot"
#endif

// Update sources.
#if defined(MCUBOOT_FSLOAD_FAT) || defined(MCUBOOT_FSLOAD_LFS2) || defined(MCUBOOT_FSLOAD_RAW)
#define MCUBOOT_FSLOAD_ENABLE 1
#else
#define MCUBOOT_FSLOAD_ENABLE 0
#if defined(MCUBOOT_FSLOAD_GZIP)
#error "MCUBOOT_FSLOAD_GZIP needs a reader: MCUBOOT_FSLOAD_FAT, MCUBOOT_FSLOAD_LFS2 or MCUBOOT_FSLOAD_RAW"
#endif
#endif

// ---- bootutil configuration ----

#define MCUBOOT_SIGN_EC256 1
#define MCUBOOT_IMAGE_NUMBER 1
#define MCUBOOT_HAVE_LOGGING 1
#define MCUBOOT_HAVE_ASSERT_H 1
#define MCUBOOT_USE_FLASH_AREA_GET_SECTORS 1
#define MCUBOOT_BOOT_TMPBUF_SZ (MCUBOOT_TMPBUF_SZ)

// ---- devices ----

#if !defined(MCUBOOT_DEV0_BASE) || !defined(MCUBOOT_DEV0_SIZE) || !defined(MCUBOOT_DEV0_WRITE) \
    || !defined(MCUBOOT_DEV0_ERASED_VAL) || !defined(MCUBOOT_DEV0_MAPPED) || !defined(MCUBOOT_DEV0_ECC)
#error "mcuboot_dev.h does not describe device 0 (MCUBOOT_DEV0_BASE, _SIZE, _WRITE, _ERASED_VAL, _MAPPED, _ECC and its erase units)"
#endif
#if !defined(MCUBOOT_BOOT_SIZE)
#error "mcuboot_dev.h or the board has to define MCUBOOT_BOOT_SIZE"
#endif

// The secondary slot and the filesystem may be on device 1.
#if defined(MCUBOOT_DEV1_BASE)
#if !defined(MCUBOOT_DEV1_SIZE) || !defined(MCUBOOT_DEV1_WRITE) \
    || !defined(MCUBOOT_DEV1_ERASED_VAL) || !defined(MCUBOOT_DEV1_MAPPED) || !defined(MCUBOOT_DEV1_ECC)
#error "mcuboot_dev.h does not describe device 1 (MCUBOOT_DEV1_BASE, _SIZE, _WRITE, _ERASED_VAL, _MAPPED, _ECC and its erase units)"
#endif
#if MCUBOOT_DEV1_BASE < MCUBOOT_DEV0_BASE + MCUBOOT_DEV0_SIZE
#error "device 1 has to be above device 0"
#endif
#define MCUBOOT_DEV_OF(addr) ((addr) >= MCUBOOT_DEV1_BASE ? 1 : 0)
#define MCUBOOT_DEV_BASE(dev) ((dev) == 1 ? MCUBOOT_DEV1_BASE : MCUBOOT_DEV0_BASE)
#else
#define MCUBOOT_DEV_OF(addr) (0)
#define MCUBOOT_DEV_BASE(dev) (MCUBOOT_DEV0_BASE)
#endif

#if (MCUBOOT_DEV0_WRITE & (MCUBOOT_DEV0_WRITE - 1)) != 0 || MCUBOOT_DEV0_WRITE < 1
#error "the write unit of device 0 has to be a power of two"
#endif
#if defined(MCUBOOT_DEV1_BASE) && ((MCUBOOT_DEV1_WRITE & (MCUBOOT_DEV1_WRITE - 1)) != 0 || MCUBOOT_DEV1_WRITE < 1)
#error "the write unit of device 1 has to be a power of two"
#endif

// ---- erase units ----

// A run holds whole erase units that are a power of two and at least the write unit, and starts
// at a multiple of its erase unit.
#define MCUBOOT_RUN_OK(start, size, erase, write) \
    ((size) > 0 && (erase) >= (write) && ((erase) & ((erase) - 1)) == 0 && ((size) % (erase)) == 0 \
    && ((start) % (erase)) == 0)

#if defined(MCUBOOT_DEV0_RUNS)
#if defined(MCUBOOT_DEV0_ERASE)
#error "mcuboot_dev.h gives the erase units of device 0 twice: MCUBOOT_DEV0_ERASE and MCUBOOT_DEV0_RUNS"
#endif
#elif defined(MCUBOOT_DEV0_ERASE)
#define MCUBOOT_DEV0_RUNS 1
#define MCUBOOT_DEV0_RUN0_SIZE (MCUBOOT_DEV0_SIZE)
#define MCUBOOT_DEV0_RUN0_ERASE (MCUBOOT_DEV0_ERASE)
#else
#error "mcuboot_dev.h does not give the erase units of device 0 (MCUBOOT_DEV0_ERASE, or MCUBOOT_DEV0_RUNS with the _RUNj_SIZE and _RUNj_ERASE of every run)"
#endif
#if MCUBOOT_DEV0_RUNS < 1 || MCUBOOT_DEV0_RUNS > 4
#error "a device has one to four runs of erase units"
#endif
#if !defined(MCUBOOT_DEV0_RUN0_SIZE) || !defined(MCUBOOT_DEV0_RUN0_ERASE) \
    || (MCUBOOT_DEV0_RUNS > 1 && (!defined(MCUBOOT_DEV0_RUN1_SIZE) || !defined(MCUBOOT_DEV0_RUN1_ERASE))) \
    || (MCUBOOT_DEV0_RUNS > 2 && (!defined(MCUBOOT_DEV0_RUN2_SIZE) || !defined(MCUBOOT_DEV0_RUN2_ERASE))) \
    || (MCUBOOT_DEV0_RUNS > 3 && (!defined(MCUBOOT_DEV0_RUN3_SIZE) || !defined(MCUBOOT_DEV0_RUN3_ERASE)))
#error "mcuboot_dev.h does not give the size and the erase unit of every run of device 0"
#endif
// A run behind the last one has no bytes and the erase unit of the run before it.
#if MCUBOOT_DEV0_RUNS < 2
#define MCUBOOT_DEV0_RUN1_SIZE 0
#define MCUBOOT_DEV0_RUN1_ERASE MCUBOOT_DEV0_RUN0_ERASE
#endif
#if MCUBOOT_DEV0_RUNS < 3
#define MCUBOOT_DEV0_RUN2_SIZE 0
#define MCUBOOT_DEV0_RUN2_ERASE MCUBOOT_DEV0_RUN1_ERASE
#endif
#if MCUBOOT_DEV0_RUNS < 4
#define MCUBOOT_DEV0_RUN3_SIZE 0
#define MCUBOOT_DEV0_RUN3_ERASE MCUBOOT_DEV0_RUN2_ERASE
#endif
#define MCUBOOT_DEV0_RUN0_END (MCUBOOT_DEV0_RUN0_SIZE)
#define MCUBOOT_DEV0_RUN1_END (MCUBOOT_DEV0_RUN0_END + MCUBOOT_DEV0_RUN1_SIZE)
#define MCUBOOT_DEV0_RUN2_END (MCUBOOT_DEV0_RUN1_END + MCUBOOT_DEV0_RUN2_SIZE)
#define MCUBOOT_DEV0_RUN3_END (MCUBOOT_DEV0_RUN2_END + MCUBOOT_DEV0_RUN3_SIZE)
#if MCUBOOT_DEV0_RUN3_END != MCUBOOT_DEV0_SIZE
#error "the runs of device 0 do not add up to its size"
#endif
#if !MCUBOOT_RUN_OK(0, MCUBOOT_DEV0_RUN0_SIZE, MCUBOOT_DEV0_RUN0_ERASE, MCUBOOT_DEV0_WRITE) \
    || (MCUBOOT_DEV0_RUNS > 1 && !MCUBOOT_RUN_OK(MCUBOOT_DEV0_RUN0_END, MCUBOOT_DEV0_RUN1_SIZE, MCUBOOT_DEV0_RUN1_ERASE, MCUBOOT_DEV0_WRITE)) \
    || (MCUBOOT_DEV0_RUNS > 2 && !MCUBOOT_RUN_OK(MCUBOOT_DEV0_RUN1_END, MCUBOOT_DEV0_RUN2_SIZE, MCUBOOT_DEV0_RUN2_ERASE, MCUBOOT_DEV0_WRITE)) \
    || (MCUBOOT_DEV0_RUNS > 3 && !MCUBOOT_RUN_OK(MCUBOOT_DEV0_RUN2_END, MCUBOOT_DEV0_RUN3_SIZE, MCUBOOT_DEV0_RUN3_ERASE, MCUBOOT_DEV0_WRITE))
#error "every run of device 0 has to hold whole erase units that are a power of two and at least the write unit, and to start at a multiple of its erase unit"
#endif
#if MCUBOOT_DEV0_RUNS == 1
#define MCUBOOT_DEV0_ERASE_AT(o) (MCUBOOT_DEV0_RUN0_ERASE)
#define MCUBOOT_DEV0_RUN_END_AT(o) (MCUBOOT_DEV0_RUN0_END)
#else
#define MCUBOOT_DEV0_ERASE_AT(o) \
    ((o) < MCUBOOT_DEV0_RUN0_END ? MCUBOOT_DEV0_RUN0_ERASE : (o) < MCUBOOT_DEV0_RUN1_END ? MCUBOOT_DEV0_RUN1_ERASE \
    : (o) < MCUBOOT_DEV0_RUN2_END ? MCUBOOT_DEV0_RUN2_ERASE : MCUBOOT_DEV0_RUN3_ERASE)
#define MCUBOOT_DEV0_RUN_END_AT(o) \
    ((o) < MCUBOOT_DEV0_RUN0_END ? MCUBOOT_DEV0_RUN0_END : (o) < MCUBOOT_DEV0_RUN1_END ? MCUBOOT_DEV0_RUN1_END \
    : (o) < MCUBOOT_DEV0_RUN2_END ? MCUBOOT_DEV0_RUN2_END : MCUBOOT_DEV0_RUN3_END)
#endif

#if defined(MCUBOOT_DEV1_BASE)
#if defined(MCUBOOT_DEV1_RUNS)
#if defined(MCUBOOT_DEV1_ERASE)
#error "mcuboot_dev.h gives the erase units of device 1 twice: MCUBOOT_DEV1_ERASE and MCUBOOT_DEV1_RUNS"
#endif
#elif defined(MCUBOOT_DEV1_ERASE)
#define MCUBOOT_DEV1_RUNS 1
#define MCUBOOT_DEV1_RUN0_SIZE (MCUBOOT_DEV1_SIZE)
#define MCUBOOT_DEV1_RUN0_ERASE (MCUBOOT_DEV1_ERASE)
#else
#error "mcuboot_dev.h does not give the erase units of device 1 (MCUBOOT_DEV1_ERASE, or MCUBOOT_DEV1_RUNS with the _RUNj_SIZE and _RUNj_ERASE of every run)"
#endif
#if MCUBOOT_DEV1_RUNS < 1 || MCUBOOT_DEV1_RUNS > 4
#error "a device has one to four runs of erase units"
#endif
#if !defined(MCUBOOT_DEV1_RUN0_SIZE) || !defined(MCUBOOT_DEV1_RUN0_ERASE) \
    || (MCUBOOT_DEV1_RUNS > 1 && (!defined(MCUBOOT_DEV1_RUN1_SIZE) || !defined(MCUBOOT_DEV1_RUN1_ERASE))) \
    || (MCUBOOT_DEV1_RUNS > 2 && (!defined(MCUBOOT_DEV1_RUN2_SIZE) || !defined(MCUBOOT_DEV1_RUN2_ERASE))) \
    || (MCUBOOT_DEV1_RUNS > 3 && (!defined(MCUBOOT_DEV1_RUN3_SIZE) || !defined(MCUBOOT_DEV1_RUN3_ERASE)))
#error "mcuboot_dev.h does not give the size and the erase unit of every run of device 1"
#endif
// A run behind the last one has no bytes and the erase unit of the run before it.
#if MCUBOOT_DEV1_RUNS < 2
#define MCUBOOT_DEV1_RUN1_SIZE 0
#define MCUBOOT_DEV1_RUN1_ERASE MCUBOOT_DEV1_RUN0_ERASE
#endif
#if MCUBOOT_DEV1_RUNS < 3
#define MCUBOOT_DEV1_RUN2_SIZE 0
#define MCUBOOT_DEV1_RUN2_ERASE MCUBOOT_DEV1_RUN1_ERASE
#endif
#if MCUBOOT_DEV1_RUNS < 4
#define MCUBOOT_DEV1_RUN3_SIZE 0
#define MCUBOOT_DEV1_RUN3_ERASE MCUBOOT_DEV1_RUN2_ERASE
#endif
#define MCUBOOT_DEV1_RUN0_END (MCUBOOT_DEV1_RUN0_SIZE)
#define MCUBOOT_DEV1_RUN1_END (MCUBOOT_DEV1_RUN0_END + MCUBOOT_DEV1_RUN1_SIZE)
#define MCUBOOT_DEV1_RUN2_END (MCUBOOT_DEV1_RUN1_END + MCUBOOT_DEV1_RUN2_SIZE)
#define MCUBOOT_DEV1_RUN3_END (MCUBOOT_DEV1_RUN2_END + MCUBOOT_DEV1_RUN3_SIZE)
#if MCUBOOT_DEV1_RUN3_END != MCUBOOT_DEV1_SIZE
#error "the runs of device 1 do not add up to its size"
#endif
#if !MCUBOOT_RUN_OK(0, MCUBOOT_DEV1_RUN0_SIZE, MCUBOOT_DEV1_RUN0_ERASE, MCUBOOT_DEV1_WRITE) \
    || (MCUBOOT_DEV1_RUNS > 1 && !MCUBOOT_RUN_OK(MCUBOOT_DEV1_RUN0_END, MCUBOOT_DEV1_RUN1_SIZE, MCUBOOT_DEV1_RUN1_ERASE, MCUBOOT_DEV1_WRITE)) \
    || (MCUBOOT_DEV1_RUNS > 2 && !MCUBOOT_RUN_OK(MCUBOOT_DEV1_RUN1_END, MCUBOOT_DEV1_RUN2_SIZE, MCUBOOT_DEV1_RUN2_ERASE, MCUBOOT_DEV1_WRITE)) \
    || (MCUBOOT_DEV1_RUNS > 3 && !MCUBOOT_RUN_OK(MCUBOOT_DEV1_RUN2_END, MCUBOOT_DEV1_RUN3_SIZE, MCUBOOT_DEV1_RUN3_ERASE, MCUBOOT_DEV1_WRITE))
#error "every run of device 1 has to hold whole erase units that are a power of two and at least the write unit, and to start at a multiple of its erase unit"
#endif
#if MCUBOOT_DEV1_RUNS == 1
#define MCUBOOT_DEV1_ERASE_AT(o) (MCUBOOT_DEV1_RUN0_ERASE)
#define MCUBOOT_DEV1_RUN_END_AT(o) (MCUBOOT_DEV1_RUN0_END)
#else
#define MCUBOOT_DEV1_ERASE_AT(o) \
    ((o) < MCUBOOT_DEV1_RUN0_END ? MCUBOOT_DEV1_RUN0_ERASE : (o) < MCUBOOT_DEV1_RUN1_END ? MCUBOOT_DEV1_RUN1_ERASE \
    : (o) < MCUBOOT_DEV1_RUN2_END ? MCUBOOT_DEV1_RUN2_ERASE : MCUBOOT_DEV1_RUN3_ERASE)
#define MCUBOOT_DEV1_RUN_END_AT(o) \
    ((o) < MCUBOOT_DEV1_RUN0_END ? MCUBOOT_DEV1_RUN0_END : (o) < MCUBOOT_DEV1_RUN1_END ? MCUBOOT_DEV1_RUN1_END \
    : (o) < MCUBOOT_DEV1_RUN2_END ? MCUBOOT_DEV1_RUN2_END : MCUBOOT_DEV1_RUN3_END)
#endif
#endif

// Lookups by CPU address. The erase unit of an address, the end of the run it lies in, and its
// offset in its device. They stand for the run table that mcuboot_devs[] holds at run time. An
// offset is an unsigned difference, so that the preprocessor never compares a negative signed
// value with an unsigned run size, which it warns about.
#define MCUBOOT_OFS(a, base) ((0u + (a)) - (0u + (base)))
#if defined(MCUBOOT_DEV1_BASE)
#define MCUBOOT_OFFSET_AT(a) \
    ((a) >= MCUBOOT_DEV1_BASE ? MCUBOOT_OFS(a, MCUBOOT_DEV1_BASE) : MCUBOOT_OFS(a, MCUBOOT_DEV0_BASE))
#define MCUBOOT_UNIT_AT(a) \
    ((a) >= MCUBOOT_DEV1_BASE ? MCUBOOT_DEV1_ERASE_AT(MCUBOOT_OFS(a, MCUBOOT_DEV1_BASE)) \
    : MCUBOOT_DEV0_ERASE_AT(MCUBOOT_OFS(a, MCUBOOT_DEV0_BASE)))
#define MCUBOOT_RUN_END_AT(a) \
    ((a) >= MCUBOOT_DEV1_BASE ? MCUBOOT_DEV1_BASE + MCUBOOT_DEV1_RUN_END_AT(MCUBOOT_OFS(a, MCUBOOT_DEV1_BASE)) \
    : MCUBOOT_DEV0_BASE + MCUBOOT_DEV0_RUN_END_AT(MCUBOOT_OFS(a, MCUBOOT_DEV0_BASE)))
#else
#define MCUBOOT_OFFSET_AT(a) MCUBOOT_OFS(a, MCUBOOT_DEV0_BASE)
#define MCUBOOT_UNIT_AT(a) (MCUBOOT_DEV0_ERASE_AT(MCUBOOT_OFS(a, MCUBOOT_DEV0_BASE)))
#define MCUBOOT_RUN_END_AT(a) (MCUBOOT_DEV0_BASE + MCUBOOT_DEV0_RUN_END_AT(MCUBOOT_OFS(a, MCUBOOT_DEV0_BASE)))
#endif

#if MCUBOOT_POLICY_SINGLE
#define MCUBOOT_SECONDARY_DEV 0
#elif defined(MCUBOOT_DEV1_BASE) && MCUBOOT_SECONDARY_ADDR >= MCUBOOT_DEV1_BASE
#define MCUBOOT_SECONDARY_DEV 1
#else
#define MCUBOOT_SECONDARY_DEV 0
#endif

#if MCUBOOT_SECONDARY_DEV == 1
#define MCUBOOT_SECONDARY_WRITE (MCUBOOT_DEV1_WRITE)
#define MCUBOOT_SECONDARY_ECC (MCUBOOT_DEV1_ECC)
#define MCUBOOT_SECONDARY_MAPPED (MCUBOOT_DEV1_MAPPED)
#else
#define MCUBOOT_SECONDARY_WRITE (MCUBOOT_DEV0_WRITE)
// The single policy has no secondary slot: no second slot with ECC.
#if MCUBOOT_POLICY_SINGLE
#define MCUBOOT_SECONDARY_ECC 0
#else
#define MCUBOOT_SECONDARY_ECC (MCUBOOT_DEV0_ECC)
#endif
#define MCUBOOT_SECONDARY_MAPPED (MCUBOOT_DEV0_MAPPED)
#endif

// bootutil places every trailer field at a multiple of this and writes them in units of the
// write size of the slot device. It is the largest write unit of the two slot devices, at
// least 8.
#if MCUBOOT_DEV0_WRITE > MCUBOOT_SECONDARY_WRITE
#define MCUBOOT_MAX_WRITE_UNIT (MCUBOOT_DEV0_WRITE)
#else
#define MCUBOOT_MAX_WRITE_UNIT (MCUBOOT_SECONDARY_WRITE)
#endif
#if MCUBOOT_MAX_WRITE_UNIT > 8
#define MCUBOOT_BOOT_MAX_ALIGN MCUBOOT_MAX_WRITE_UNIT
#define MCUBOOT_MAX_ALIGN_VALUE MCUBOOT_MAX_WRITE_UNIT
#else
#define MCUBOOT_MAX_ALIGN_VALUE 8
#endif
#if MCUBOOT_MAX_ALIGN_VALUE > 32
#error "the write unit of a slot device is larger than 32 bytes"
#endif
#if (MCUBOOT_MAX_ALIGN_VALUE % MCUBOOT_DEV0_WRITE) != 0 || (MCUBOOT_MAX_ALIGN_VALUE % MCUBOOT_SECONDARY_WRITE) != 0
#error "the trailer alignment is not a multiple of a slot device write unit"
#endif
// The ECC policy protects each write unit on its own, so a trailer field has to occupy exactly one.
#if MCUBOOT_DEV0_ECC && MCUBOOT_DEV0_WRITE != MCUBOOT_MAX_ALIGN_VALUE
#error "a device with ECC needs a write unit equal to the trailer alignment"
#endif

// ---- slots ----

#ifndef MCUBOOT_PRIMARY_ADDR
#define MCUBOOT_PRIMARY_ADDR (MCUBOOT_DEV0_BASE + MCUBOOT_BOOT_SIZE)
#endif
#define MCUBOOT_BOOT_ADDR (MCUBOOT_DEV0_BASE)

// The erase unit of an area is the one of the run it lies in. The slots, and the scratch and
// shadow areas that work on their sectors, have the unit of the primary slot.
#define MCUBOOT_BOOT_UNIT MCUBOOT_UNIT_AT(MCUBOOT_BOOT_ADDR)
#define MCUBOOT_PRIMARY_UNIT MCUBOOT_UNIT_AT(MCUBOOT_PRIMARY_ADDR)
#define MCUBOOT_SLOT_UNIT MCUBOOT_PRIMARY_UNIT

// Size of the secondary slot by mode: swap using offset needs a spare erase unit in front of the
// image, swap using move one in the primary slot for the move, the other modes have two slots of
// equal size.
#if MCUBOOT_POLICY_SINGLE
#if defined(MCUBOOT_SECONDARY_ADDR)
#error "MCUBOOT_POLICY_SEL_SINGLE has one slot, MCUBOOT_SECONDARY_ADDR must not be defined"
#else
#define MCUBOOT_SECONDARY_ADDR 0
#endif
#define MCUBOOT_SECONDARY_SIZE 0
#elif defined(MCUBOOT_SWAP_USING_OFFSET)
#define MCUBOOT_SECONDARY_SIZE (MCUBOOT_PRIMARY_SIZE + MCUBOOT_SLOT_UNIT)
#elif defined(MCUBOOT_SWAP_USING_MOVE)
#define MCUBOOT_SECONDARY_SIZE (MCUBOOT_PRIMARY_SIZE - MCUBOOT_SLOT_UNIT)
#else
#define MCUBOOT_SECONDARY_SIZE (MCUBOOT_PRIMARY_SIZE)
#endif
#if !MCUBOOT_POLICY_SINGLE
#define MCUBOOT_SECONDARY_UNIT MCUBOOT_UNIT_AT(MCUBOOT_SECONDARY_ADDR)
#endif
#define MCUBOOT_PRIMARY_END (MCUBOOT_PRIMARY_ADDR + MCUBOOT_PRIMARY_SIZE)
#define MCUBOOT_SECONDARY_END (MCUBOOT_SECONDARY_ADDR + MCUBOOT_SECONDARY_SIZE)

// bootutil's sector table has an entry for every sector of the larger slot.
#if MCUBOOT_SECONDARY_SIZE > MCUBOOT_PRIMARY_SIZE
#define MCUBOOT_MAX_IMG_SECTORS (MCUBOOT_SECONDARY_SIZE / MCUBOOT_SLOT_UNIT)
#else
#define MCUBOOT_MAX_IMG_SECTORS (MCUBOOT_PRIMARY_SIZE / MCUBOOT_SLOT_UNIT)
#endif

// Trailer of a slot, as bootutil_area.c sizes it. The swap modes keep two states (offset) or
// three (move, scratch) per sector, then the swap info fields and the magic. Overwrite-external
// and single have no status table: the flag words and the magic only (single has one flag word
// more than overwrite-external).
#define MCUBOOT_ALIGN_UP(x, a) ((((x) + (a) - 1) / (a)) * (a))
#define MCUBOOT_TRAILER_MAGIC_SIZE MCUBOOT_ALIGN_UP(16, MCUBOOT_MAX_ALIGN_VALUE)
#if MCUBOOT_POLICY_SWAP
#if defined(MCUBOOT_SWAP_USING_OFFSET)
#define MCUBOOT_TRAILER_SIZE \
    (MCUBOOT_MAX_IMG_SECTORS * 2 * MCUBOOT_MAX_WRITE_UNIT + MCUBOOT_MAX_ALIGN_VALUE * 5 \
    + MCUBOOT_TRAILER_MAGIC_SIZE)
#else
#define MCUBOOT_TRAILER_SIZE \
    (MCUBOOT_MAX_IMG_SECTORS * 3 * MCUBOOT_MAX_WRITE_UNIT + MCUBOOT_MAX_ALIGN_VALUE * 4 \
    + MCUBOOT_TRAILER_MAGIC_SIZE)
#endif
#define MCUBOOT_TRAILER_SECTORS ((MCUBOOT_TRAILER_SIZE + MCUBOOT_SLOT_UNIT - 1) / MCUBOOT_SLOT_UNIT)
// Swap using move keeps one sector of the primary slot free for the move.
#if defined(MCUBOOT_SWAP_USING_MOVE)
#define MCUBOOT_MAX_IMAGE_SIZE (MCUBOOT_PRIMARY_SIZE - (MCUBOOT_TRAILER_SECTORS + 1) * MCUBOOT_SLOT_UNIT)
#else
#define MCUBOOT_MAX_IMAGE_SIZE (MCUBOOT_PRIMARY_SIZE - MCUBOOT_TRAILER_SECTORS * MCUBOOT_SLOT_UNIT)
#endif
#else
#if MCUBOOT_POLICY_OVERWRITE_EXTERNAL
#define MCUBOOT_TRAILER_SIZE (MCUBOOT_MAX_ALIGN_VALUE * 3 + MCUBOOT_TRAILER_MAGIC_SIZE)
#else
#define MCUBOOT_TRAILER_SIZE (MCUBOOT_MAX_ALIGN_VALUE * 4 + MCUBOOT_TRAILER_MAGIC_SIZE)
#endif
#define MCUBOOT_TRAILER_SECTORS ((MCUBOOT_TRAILER_SIZE + MCUBOOT_SLOT_UNIT - 1) / MCUBOOT_SLOT_UNIT)
#define MCUBOOT_MAX_IMAGE_SIZE (MCUBOOT_PRIMARY_SIZE - MCUBOOT_TRAILER_SIZE)
#endif

// The slot an update is written to and what it holds: the secondary slot, or the only slot of
// the single policy. Swap using offset leaves the first erase unit of the secondary slot for the
// image that is replaced.
#if MCUBOOT_POLICY_SINGLE
#define MCUBOOT_UPDATE_ADDR (MCUBOOT_PRIMARY_ADDR)
#define MCUBOOT_UPDATE_SIZE (MCUBOOT_PRIMARY_SIZE)
#else
#define MCUBOOT_UPDATE_ADDR (MCUBOOT_SECONDARY_ADDR)
#define MCUBOOT_UPDATE_SIZE (MCUBOOT_SECONDARY_SIZE)
#endif
#if defined(MCUBOOT_SWAP_USING_OFFSET)
#define MCUBOOT_UPDATE_SPARE (MCUBOOT_SLOT_UNIT)
#else
#define MCUBOOT_UPDATE_SPARE 0
#endif
#define MCUBOOT_UPDATE_END (MCUBOOT_UPDATE_ADDR + MCUBOOT_UPDATE_SIZE)

// The update slot holds an image from after its spare unit up to its trailer sectors. The
// single policy has no swap state: its image may use the whole slot, up to the trailer reservation
// that MCUBOOT_MAX_IMAGE_SIZE leaves. The application is linked to fit there, and in the primary
// slot.
#if MCUBOOT_POLICY_SINGLE
#define MCUBOOT_UPDATE_TRAILER_OFF (MCUBOOT_UPDATE_SIZE)
#else
#define MCUBOOT_UPDATE_TRAILER_OFF (MCUBOOT_UPDATE_SIZE - MCUBOOT_TRAILER_SECTORS * MCUBOOT_SLOT_UNIT)
#endif
#define MCUBOOT_UPDATE_IMAGE_SIZE (MCUBOOT_UPDATE_TRAILER_OFF - MCUBOOT_UPDATE_SPARE)
#if MCUBOOT_UPDATE_IMAGE_SIZE < MCUBOOT_MAX_IMAGE_SIZE
#define MCUBOOT_APP_LIMIT (MCUBOOT_UPDATE_IMAGE_SIZE)
#else
#define MCUBOOT_APP_LIMIT (MCUBOOT_MAX_IMAGE_SIZE)
#endif

// Swap using offset only resumes an interrupted swap of an image that, with its header and TLVs,
// is larger than one erase unit: for a smaller one bootutil's boot_read_image_header() reads the
// header of the image under swap from the first unit of the primary slot, which a cut inside the
// erase of that unit leaves neither valid nor erased, and the swap is not resumed. An update of
// at most one erase unit is refused (MCUBOOT_RES_ERR_TOO_SMALL, or -EINVAL from the application
// writer). The other modes and policies take any image.
#if defined(MCUBOOT_SWAP_USING_OFFSET)
#define MCUBOOT_MIN_IMAGE_SIZE (MCUBOOT_SLOT_UNIT + 1)
#else
#define MCUBOOT_MIN_IMAGE_SIZE 0
#endif

// ---- areas next to the primary slot ----

#if defined(MCUBOOT_SECCNT_FLASH)
#ifndef MCUBOOT_SECCNT_ADDR
#define MCUBOOT_SECCNT_ADDR (MCUBOOT_PRIMARY_END)
#endif
#define MCUBOOT_SECCNT_UNIT MCUBOOT_UNIT_AT(MCUBOOT_SECCNT_ADDR)
#define MCUBOOT_SECCNT_SIZE (2 * MCUBOOT_SECCNT_UNIT)
#define MCUBOOT_AUX_AFTER_SECCNT (MCUBOOT_SECCNT_ADDR + MCUBOOT_SECCNT_SIZE)
#else
#if defined(MCUBOOT_SECCNT_ADDR)
#error "MCUBOOT_SECCNT_ADDR needs MCUBOOT_ROLLBACK_COUNTER 1 with the counter in flash"
#endif
#define MCUBOOT_AUX_AFTER_SECCNT (MCUBOOT_PRIMARY_END)
#endif

// One shadow sector for each trailer sector of each slot that is on a device with ECC.
#if MCUBOOT_DEV0_ECC
#ifndef MCUBOOT_SHADOW_ADDR
#define MCUBOOT_SHADOW_ADDR (MCUBOOT_AUX_AFTER_SECCNT)
#endif
#define MCUBOOT_ECC_SHADOW 1
#define MCUBOOT_SHADOW_UNIT MCUBOOT_UNIT_AT(MCUBOOT_SHADOW_ADDR)
#define MCUBOOT_SHADOW_SIZE ((1 + MCUBOOT_SECONDARY_ECC) * MCUBOOT_TRAILER_SECTORS * MCUBOOT_SHADOW_UNIT)
#define MCUBOOT_AUX_END_SHADOW (MCUBOOT_SHADOW_ADDR + MCUBOOT_SHADOW_SIZE)
#else
#if defined(MCUBOOT_SHADOW_ADDR)
#error "MCUBOOT_SHADOW_ADDR only applies to a device 0 with ECC"
#endif
#define MCUBOOT_AUX_END_SHADOW (MCUBOOT_AUX_AFTER_SECCNT)
#endif

// The scratch area of swap using scratch.
#if defined(MCUBOOT_SWAP_USING_SCRATCH)
// A scratch area that the board places does not move the default of the filesystem.
#ifndef MCUBOOT_SCRATCH_ADDR
#define MCUBOOT_SCRATCH_ADDR (MCUBOOT_AUX_END_SHADOW)
#define MCUBOOT_SCRATCH_DEFAULT 1
#endif
#define MCUBOOT_SCRATCH_UNIT MCUBOOT_UNIT_AT(MCUBOOT_SCRATCH_ADDR)
#ifndef MCUBOOT_SCRATCH_SIZE
#define MCUBOOT_SCRATCH_SIZE (MCUBOOT_SCRATCH_UNIT)
#endif
#if MCUBOOT_SCRATCH_SIZE < MCUBOOT_SCRATCH_UNIT
#error "the scratch area has to be at least one erase unit (the largest sector)"
#endif
#if defined(MCUBOOT_SCRATCH_DEFAULT)
#define MCUBOOT_AUX_END_SCRATCH (MCUBOOT_SCRATCH_ADDR + MCUBOOT_SCRATCH_SIZE)
#else
#define MCUBOOT_AUX_END_SCRATCH (MCUBOOT_AUX_END_SHADOW)
#endif
#else
#if defined(MCUBOOT_SCRATCH_ADDR) || defined(MCUBOOT_SCRATCH_SIZE)
#error "MCUBOOT_SCRATCH_ADDR and MCUBOOT_SCRATCH_SIZE only apply to MCUBOOT_SWAP_MODE_SEL_SCRATCH"
#endif
#define MCUBOOT_AUX_END_SCRATCH (MCUBOOT_AUX_END_SHADOW)
#endif

// The intent area of the single policy with fsload: the element stream of a request is saved
// there before the only slot is overwritten, so that a power loss during the install repeats it.
#if MCUBOOT_POLICY_SINGLE && MCUBOOT_FSLOAD_ENABLE
// An intent area that the board places does not move the default of the filesystem.
#ifndef MCUBOOT_INTENT_ADDR
#define MCUBOOT_INTENT_ADDR (MCUBOOT_AUX_END_SCRATCH)
#define MCUBOOT_INTENT_DEFAULT 1
#endif
#define MCUBOOT_INTENT_UNIT MCUBOOT_UNIT_AT(MCUBOOT_INTENT_ADDR)
#define MCUBOOT_INTENT_SIZE (MCUBOOT_INTENT_UNIT)
#if defined(MCUBOOT_INTENT_DEFAULT)
#define MCUBOOT_AUX_END (MCUBOOT_INTENT_ADDR + MCUBOOT_INTENT_SIZE)
#else
#define MCUBOOT_AUX_END (MCUBOOT_AUX_END_SCRATCH)
#endif
#else
#if defined(MCUBOOT_INTENT_ADDR)
#error "MCUBOOT_INTENT_ADDR only applies to MCUBOOT_POLICY_SEL_SINGLE with fsload"
#endif
#define MCUBOOT_AUX_END (MCUBOOT_AUX_END_SCRATCH)
#endif

#if !defined(MCUBOOT_FS_ADDR)
#define MCUBOOT_FS_ADDR (MCUBOOT_AUX_END)
#if MCUBOOT_SECONDARY_DEV == 0 && !MCUBOOT_POLICY_SINGLE
#define MCUBOOT_FS_SIZE (MCUBOOT_SECONDARY_ADDR - MCUBOOT_FS_ADDR)
#else
#define MCUBOOT_FS_SIZE (MCUBOOT_DEV0_BASE + MCUBOOT_DEV0_SIZE - MCUBOOT_FS_ADDR)
#endif
#elif !defined(MCUBOOT_FS_SIZE)
#error "MCUBOOT_FS_ADDR needs MCUBOOT_FS_SIZE"
#endif
#if MCUBOOT_FS_SIZE <= 0
#error "the filesystem has no room: place it with MCUBOOT_FS_ADDR and MCUBOOT_FS_SIZE"
#endif
#define MCUBOOT_FS_UNIT MCUBOOT_UNIT_AT(MCUBOOT_FS_ADDR)

// ---- checks ----

#define MCUBOOT_OVERLAPS(a, asz, b, bsz) \
    ((asz) > 0 && (bsz) > 0 && (a) < (b) + (bsz) && (b) < (a) + (asz))

#if !defined(MCUBOOT_PRIMARY_SIZE) || (!MCUBOOT_POLICY_SINGLE && !defined(MCUBOOT_SECONDARY_ADDR))
#error "the board has to define MCUBOOT_PRIMARY_SIZE and MCUBOOT_SECONDARY_ADDR (MCUBOOT_SECONDARY_ADDR not with MCUBOOT_POLICY_SEL_SINGLE)"
#endif
#if !defined(MCUBOOT_ROLLBACK_COUNTER)
#error "the board has to define MCUBOOT_ROLLBACK_COUNTER (1 for a security counter, 0 for a version check)"
#endif

// Every area as an address and a size. An area that the configuration does not have has size 0 and
// the address of the start of device 0.
#define MCUBOOT_CHK_BOOT_ADDR (MCUBOOT_BOOT_ADDR)
#define MCUBOOT_CHK_BOOT_SIZE (MCUBOOT_BOOT_SIZE)
#define MCUBOOT_CHK_PRIMARY_ADDR (MCUBOOT_PRIMARY_ADDR)
#define MCUBOOT_CHK_PRIMARY_SIZE (MCUBOOT_PRIMARY_SIZE)
#if !MCUBOOT_POLICY_SINGLE
#define MCUBOOT_CHK_SECONDARY_ADDR (MCUBOOT_SECONDARY_ADDR)
#define MCUBOOT_CHK_SECONDARY_SIZE (MCUBOOT_SECONDARY_SIZE)
#else
#define MCUBOOT_CHK_SECONDARY_ADDR (MCUBOOT_DEV0_BASE)
#define MCUBOOT_CHK_SECONDARY_SIZE 0
#endif
#if defined(MCUBOOT_SECCNT_FLASH)
#define MCUBOOT_CHK_SECCNT_ADDR (MCUBOOT_SECCNT_ADDR)
#define MCUBOOT_CHK_SECCNT_SIZE (MCUBOOT_SECCNT_SIZE)
#else
#define MCUBOOT_CHK_SECCNT_ADDR (MCUBOOT_DEV0_BASE)
#define MCUBOOT_CHK_SECCNT_SIZE 0
#endif
#if defined(MCUBOOT_ECC_SHADOW)
#define MCUBOOT_CHK_SHADOW_ADDR (MCUBOOT_SHADOW_ADDR)
#define MCUBOOT_CHK_SHADOW_SIZE (MCUBOOT_SHADOW_SIZE)
#else
#define MCUBOOT_CHK_SHADOW_ADDR (MCUBOOT_DEV0_BASE)
#define MCUBOOT_CHK_SHADOW_SIZE 0
#endif
#if defined(MCUBOOT_SWAP_USING_SCRATCH)
#define MCUBOOT_CHK_SCRATCH_ADDR (MCUBOOT_SCRATCH_ADDR)
#define MCUBOOT_CHK_SCRATCH_SIZE (MCUBOOT_SCRATCH_SIZE)
#else
#define MCUBOOT_CHK_SCRATCH_ADDR (MCUBOOT_DEV0_BASE)
#define MCUBOOT_CHK_SCRATCH_SIZE 0
#endif
#if defined(MCUBOOT_INTENT_ADDR)
#define MCUBOOT_CHK_INTENT_ADDR (MCUBOOT_INTENT_ADDR)
#define MCUBOOT_CHK_INTENT_SIZE (MCUBOOT_INTENT_SIZE)
#else
#define MCUBOOT_CHK_INTENT_ADDR (MCUBOOT_DEV0_BASE)
#define MCUBOOT_CHK_INTENT_SIZE 0
#endif
#define MCUBOOT_CHK_FS_ADDR (MCUBOOT_FS_ADDR)
#define MCUBOOT_CHK_FS_SIZE (MCUBOOT_FS_SIZE)

// An area lies in one run of erase units, starts at and is a multiple of the erase unit of the run.
#define MCUBOOT_CHK_UNITS(A) \
    (MCUBOOT_CHK_##A##_SIZE == 0 \
    || (MCUBOOT_CHK_##A##_ADDR + MCUBOOT_CHK_##A##_SIZE <= MCUBOOT_RUN_END_AT(MCUBOOT_CHK_##A##_ADDR) \
    && (MCUBOOT_OFFSET_AT(MCUBOOT_CHK_##A##_ADDR) % MCUBOOT_UNIT_AT(MCUBOOT_CHK_##A##_ADDR)) == 0 \
    && (MCUBOOT_CHK_##A##_SIZE % MCUBOOT_UNIT_AT(MCUBOOT_CHK_##A##_ADDR)) == 0))
// An area inside device 0.
#define MCUBOOT_CHK_IN_DEV0(A) \
    (MCUBOOT_CHK_##A##_SIZE == 0 \
    || (MCUBOOT_CHK_##A##_ADDR >= MCUBOOT_DEV0_BASE \
    && MCUBOOT_CHK_##A##_ADDR + MCUBOOT_CHK_##A##_SIZE <= MCUBOOT_DEV0_BASE + MCUBOOT_DEV0_SIZE))
#define MCUBOOT_CHK_OVERLAP(A, B) \
    MCUBOOT_OVERLAPS(MCUBOOT_CHK_##A##_ADDR, MCUBOOT_CHK_##A##_SIZE, MCUBOOT_CHK_##B##_ADDR, MCUBOOT_CHK_##B##_SIZE)

#if !MCUBOOT_CHK_UNITS(BOOT)
#error "the bootloader region has to lie in one run of erase units, start at and be a multiple of the erase unit"
#endif
#if !MCUBOOT_CHK_UNITS(PRIMARY)
#error "the primary slot has to lie in one run of erase units, start at and be a multiple of the erase unit"
#endif
#if !MCUBOOT_CHK_UNITS(SECONDARY)
#error "the secondary slot has to lie in one run of erase units, start at and be a multiple of the erase unit"
#endif
#if !MCUBOOT_CHK_UNITS(SECCNT)
#error "the counter area has to lie in one run of erase units, start at and be a multiple of the erase unit"
#endif
#if !MCUBOOT_CHK_UNITS(SHADOW)
#error "the shadow area has to lie in one run of erase units, start at and be a multiple of the erase unit"
#endif
#if !MCUBOOT_CHK_UNITS(SCRATCH)
#error "the scratch area has to lie in one run of erase units, start at and be a multiple of the erase unit, and be at least one erase unit (the largest sector)"
#endif
#if !MCUBOOT_CHK_UNITS(INTENT)
#error "the intent area has to lie in one run of erase units and start at a multiple of the erase unit"
#endif
#if !MCUBOOT_CHK_UNITS(FS)
#error "the filesystem has to lie in one run of erase units, start at and be a multiple of the erase unit"
#endif

#if MCUBOOT_DEV_OF(MCUBOOT_PRIMARY_ADDR) != 0
#error "the primary slot has to be in device 0"
#endif
#if !MCUBOOT_CHK_IN_DEV0(BOOT)
#error "the bootloader region does not fit in device 0"
#endif
#if !MCUBOOT_CHK_IN_DEV0(PRIMARY)
#error "the primary slot does not fit in device 0"
#endif
#if !MCUBOOT_CHK_IN_DEV0(SECCNT)
#error "the counter area does not fit in device 0"
#endif
#if !MCUBOOT_CHK_IN_DEV0(SHADOW)
#error "the shadow area does not fit in device 0"
#endif
#if !MCUBOOT_CHK_IN_DEV0(SCRATCH)
#error "the scratch area does not fit in device 0"
#endif
#if !MCUBOOT_CHK_IN_DEV0(INTENT)
#error "the intent area does not fit in device 0"
#endif
#if !MCUBOOT_POLICY_SINGLE
#if MCUBOOT_SECONDARY_DEV == 0
#if MCUBOOT_SECONDARY_END > MCUBOOT_DEV0_BASE + MCUBOOT_DEV0_SIZE
#error "the secondary slot does not fit in device 0"
#endif
#else
#if MCUBOOT_SECONDARY_END > MCUBOOT_DEV1_BASE + MCUBOOT_DEV1_SIZE
#error "the secondary slot does not fit in device 1"
#endif
#endif
#if MCUBOOT_SECONDARY_UNIT != MCUBOOT_PRIMARY_UNIT
#error "the erase unit of the secondary slot device differs from the one of the primary slot"
#endif
#endif
#if MCUBOOT_DEV_OF(MCUBOOT_FS_ADDR) == 0
#if MCUBOOT_FS_ADDR + MCUBOOT_FS_SIZE > MCUBOOT_DEV0_BASE + MCUBOOT_DEV0_SIZE
#error "the filesystem does not fit in device 0"
#endif
#elif MCUBOOT_FS_ADDR + MCUBOOT_FS_SIZE > MCUBOOT_DEV1_BASE + MCUBOOT_DEV1_SIZE
#error "the filesystem does not fit in device 1"
#endif

#if defined(MCUBOOT_ECC_SHADOW) && MCUBOOT_SHADOW_UNIT != MCUBOOT_SLOT_UNIT
#error "the shadow area needs the erase unit of the slots: it holds one sector for every sector of their trailers"
#endif
// The scratch area takes the sectors of a slot one at a time.
#if defined(MCUBOOT_SWAP_USING_SCRATCH) && MCUBOOT_SCRATCH_UNIT != MCUBOOT_SLOT_UNIT
#error "the scratch area needs the erase unit of the slots"
#endif

// No two areas overlap. The filesystem on device 1 and the secondary slot there are told apart
// by their addresses.
#if MCUBOOT_CHK_OVERLAP(BOOT, PRIMARY)
#error "the bootloader region overlaps the primary slot"
#elif MCUBOOT_CHK_OVERLAP(BOOT, SECONDARY)
#error "the bootloader region overlaps the secondary slot"
#elif MCUBOOT_CHK_OVERLAP(BOOT, SECCNT)
#error "the bootloader region overlaps the counter area"
#elif MCUBOOT_CHK_OVERLAP(BOOT, SHADOW)
#error "the bootloader region overlaps the shadow area"
#elif MCUBOOT_CHK_OVERLAP(BOOT, SCRATCH)
#error "the bootloader region overlaps the scratch area"
#elif MCUBOOT_CHK_OVERLAP(BOOT, INTENT)
#error "the bootloader region overlaps the intent area"
#elif MCUBOOT_CHK_OVERLAP(BOOT, FS)
#error "the bootloader region overlaps the filesystem"
#elif MCUBOOT_CHK_OVERLAP(PRIMARY, SECONDARY)
#error "the primary slot overlaps the secondary slot"
#elif MCUBOOT_CHK_OVERLAP(PRIMARY, SECCNT)
#error "the primary slot overlaps the counter area"
#elif MCUBOOT_CHK_OVERLAP(PRIMARY, SHADOW)
#error "the primary slot overlaps the shadow area"
#elif MCUBOOT_CHK_OVERLAP(PRIMARY, SCRATCH)
#error "the primary slot overlaps the scratch area"
#elif MCUBOOT_CHK_OVERLAP(PRIMARY, INTENT)
#error "the primary slot overlaps the intent area"
#elif MCUBOOT_CHK_OVERLAP(PRIMARY, FS)
#error "the filesystem overlaps the primary slot"
#elif MCUBOOT_CHK_OVERLAP(SECONDARY, SECCNT)
#error "the secondary slot overlaps the counter area"
#elif MCUBOOT_CHK_OVERLAP(SECONDARY, SHADOW)
#error "the secondary slot overlaps the shadow area"
#elif MCUBOOT_CHK_OVERLAP(SECONDARY, SCRATCH)
#error "the secondary slot overlaps the scratch area"
#elif MCUBOOT_CHK_OVERLAP(SECONDARY, INTENT)
#error "the secondary slot overlaps the intent area"
#elif MCUBOOT_CHK_OVERLAP(SECONDARY, FS)
#error "the filesystem overlaps the secondary slot"
#elif MCUBOOT_CHK_OVERLAP(SECCNT, SHADOW)
#error "the counter area overlaps the shadow area"
#elif MCUBOOT_CHK_OVERLAP(SECCNT, SCRATCH)
#error "the counter area overlaps the scratch area"
#elif MCUBOOT_CHK_OVERLAP(SECCNT, INTENT)
#error "the counter area overlaps the intent area"
#elif MCUBOOT_CHK_OVERLAP(SECCNT, FS)
#error "the filesystem overlaps the counter area"
#elif MCUBOOT_CHK_OVERLAP(SHADOW, SCRATCH)
#error "the shadow area overlaps the scratch area"
#elif MCUBOOT_CHK_OVERLAP(SHADOW, INTENT)
#error "the shadow area overlaps the intent area"
#elif MCUBOOT_CHK_OVERLAP(SHADOW, FS)
#error "the filesystem overlaps the shadow area"
#elif MCUBOOT_CHK_OVERLAP(SCRATCH, INTENT)
#error "the scratch area overlaps the intent area"
#elif MCUBOOT_CHK_OVERLAP(SCRATCH, FS)
#error "the scratch area overlaps the filesystem"
#elif MCUBOOT_CHK_OVERLAP(INTENT, FS)
#error "the intent area overlaps the filesystem"
#endif

#if defined(MCUBOOT_SWAP_USING_MOVE) && MCUBOOT_PRIMARY_SIZE < 2 * MCUBOOT_SLOT_UNIT
#error "swap using move needs a primary slot of at least two erase units"
#endif
// The shadow words of the ECC policy cover the trailers of the two slots only; the swap status
// that scratch swap keeps in the scratch area would not survive a torn flash word.
#if defined(MCUBOOT_SWAP_USING_SCRATCH) && MCUBOOT_DEV0_ECC
#error "swap using scratch is not supported on a device with ECC: the swap status in the scratch area is not covered by the shadow area"
#endif

#if (MCUBOOT_HEADER_SIZE % MCUBOOT_VTOR_ALIGN) != 0 || (MCUBOOT_HEADER_SIZE % MCUBOOT_MAX_ALIGN_VALUE) != 0 \
    || MCUBOOT_HEADER_SIZE < 0x20 || (MCUBOOT_PRIMARY_ADDR % MCUBOOT_VTOR_ALIGN) != 0
#error "the image header size and the primary slot address have to be multiples of the vector table alignment"
#endif
#if MCUBOOT_APP_LIMIT <= MCUBOOT_HEADER_SIZE + MCUBOOT_TLV_RESERVE
#error "the header and the TLV reserve leave no room in the maximum image size"
#endif
#if MCUBOOT_UPDATE_IMAGE_SIZE <= MCUBOOT_HEADER_SIZE + MCUBOOT_TLV_RESERVE
#error "the update slot leaves no room for an image before its trailer"
#endif
#if (MCUBOOT_TMPBUF_SZ % MCUBOOT_MAX_ALIGN_VALUE) != 0
#error "MCUBOOT_TMPBUF_SZ has to be a multiple of the trailer alignment"
#endif
#if MCUBOOT_MAX_IMG_SECTORS > 4096
#error "the slots have more than 4096 sectors"
#endif
#if MCUBOOT_DEV0_ECC && !MCUBOOT_DFU_ENABLE
#error "a device with ECC needs the DFU front end, it is the way out of a torn flash word"
#endif
#if MCUBOOT_LOG_LEVEL < 0 || MCUBOOT_LOG_LEVEL > 4
#error "MCUBOOT_LOG_LEVEL is 0 to 4"
#endif
#if MCUBOOT_SECURITY_COUNTER < 0 || MCUBOOT_SECURITY_COUNTER > 0xFFFFFFFF
#error "MCUBOOT_SECURITY_COUNTER does not fit in 32 bits"
#endif
#if MCUBOOT_DFU_ENABLE && (MCUBOOT_DFU_VID < 1 || MCUBOOT_DFU_VID > 0xFFFF || MCUBOOT_DFU_PID < 1 || MCUBOOT_DFU_PID > 0xFFFF)
#error "MCUBOOT_DFU_VID and MCUBOOT_DFU_PID are 16 bit USB ids"
#endif
#if MCUBOOT_HASH_DIRECT
#if !MCUBOOT_DEV0_MAPPED || !MCUBOOT_SECONDARY_MAPPED
#error "MCUBOOT_HASH_DIRECT needs memory mapped slot devices (hashing reads the flash in place)"
#endif
// bootutil_img_hash() with MCUBOOT_HASH_STORAGE_DIRECTLY hashes from the start of the area and
// leaves out the erase unit that swap using offset puts in front of the image in the secondary
// slot, so a valid secondary image would fail validation.
#if defined(MCUBOOT_SWAP_USING_OFFSET)
#error "MCUBOOT_HASH_DIRECT is not supported with swap using offset: direct hashing ignores the erase unit in front of the image in the secondary slot"
#endif
// The fsload stream area has no flash at the device base to read in place.
#if MCUBOOT_FSLOAD_ENABLE
#error "MCUBOOT_HASH_DIRECT cannot hash the fsload stream area: not with fsload"
#endif
#endif

// The primary slot, its trailer and everything else the bootloader trusts for a decision stay on
// device 0, which runs the application. Device 1 only holds data that is validated before use:
// the secondary slot (by signature) and the filesystem (read only). The shadow area of the ECC
// policy covers device 0 only.
#if defined(MCUBOOT_DEV1_BASE)
#if MCUBOOT_DEV1_ECC
#error "device 1 has no ECC in this configuration"
#endif
// Device 1 is a SPI flash behind drivers/memory/spiflash.c, which erases blocks of 4 KiB.
#if (MCUBOOT_DEV1_RUN0_ERASE % 4096) != 0 || (MCUBOOT_DEV1_RUNS > 1 && (MCUBOOT_DEV1_RUN1_ERASE % 4096) != 0) \
    || (MCUBOOT_DEV1_RUNS > 2 && (MCUBOOT_DEV1_RUN2_ERASE % 4096) != 0) \
    || (MCUBOOT_DEV1_RUNS > 3 && (MCUBOOT_DEV1_RUN3_ERASE % 4096) != 0)
#error "the erase unit of device 1 has to be a multiple of the 4 KiB erase block of a SPI flash"
#endif
// Trailer fields of a secondary slot on SPI flash are one write unit each. The SPI NOR tests only
// cover that case.
#if MCUBOOT_SECONDARY_DEV == 1 && MCUBOOT_DEV1_WRITE != MCUBOOT_MAX_ALIGN_VALUE
#error "the write unit of a secondary slot on device 1 has to equal the trailer alignment"
#endif
#endif

// ---- identity ----

// Binds a signed image to the flash layout it was linked for (custom TLV of the image):
// FNV-1 over the 32 bit words below. The default swap-using-offset configuration uses the first
// ten words; other policies, swap modes, counter backends or version checks, and counter or shadow
// areas placed away from their defaults add more. tools/mcuboot_gen.py reads the same expression.
#define MCUBOOT_ID_STEP(h, w) ((((h) ^ ((w) & 0xFFFFFFFFu)) * 16777619u) & 0xFFFFFFFFu)
#define MCUBOOT_ID_0 0x811C9DC5u
#define MCUBOOT_ID_1 MCUBOOT_ID_STEP(MCUBOOT_ID_0, MCUBOOT_PRIMARY_ADDR)
#define MCUBOOT_ID_2 MCUBOOT_ID_STEP(MCUBOOT_ID_1, MCUBOOT_PRIMARY_SIZE)
#define MCUBOOT_ID_3 MCUBOOT_ID_STEP(MCUBOOT_ID_2, MCUBOOT_SECONDARY_ADDR)
#define MCUBOOT_ID_4 MCUBOOT_ID_STEP(MCUBOOT_ID_3, MCUBOOT_SECONDARY_SIZE)
#define MCUBOOT_ID_5 MCUBOOT_ID_STEP(MCUBOOT_ID_4, MCUBOOT_FS_ADDR)
#define MCUBOOT_ID_6 MCUBOOT_ID_STEP(MCUBOOT_ID_5, MCUBOOT_FS_SIZE)
#define MCUBOOT_ID_7 MCUBOOT_ID_STEP(MCUBOOT_ID_6, MCUBOOT_HEADER_SIZE)
#define MCUBOOT_ID_8 MCUBOOT_ID_STEP(MCUBOOT_ID_7, MCUBOOT_SLOT_UNIT)
#define MCUBOOT_ID_9 MCUBOOT_ID_STEP(MCUBOOT_ID_8, MCUBOOT_MAX_WRITE_UNIT)
#define MCUBOOT_ID_10 MCUBOOT_ID_STEP(MCUBOOT_ID_9, MCUBOOT_ROLLBACK_COUNTER)

// Bit 0..2: policy and swap mode (0 swap using offset, 1 move, 2 scratch, 3 overwrite-external,
// 4 single), bit 3: the counter is kept by the port, bit 4: no version check.
#if MCUBOOT_POLICY_SINGLE
#define MCUBOOT_ID_MODE 4
#elif MCUBOOT_POLICY_OVERWRITE_EXTERNAL
#define MCUBOOT_ID_MODE 3
#elif defined(MCUBOOT_SWAP_USING_SCRATCH)
#define MCUBOOT_ID_MODE 2
#elif defined(MCUBOOT_SWAP_USING_MOVE)
#define MCUBOOT_ID_MODE 1
#else
#define MCUBOOT_ID_MODE 0
#endif
#if defined(MCUBOOT_SECCNT_PORT)
#define MCUBOOT_ID_COUNTER 8
#else
#define MCUBOOT_ID_COUNTER 0
#endif
#if !MCUBOOT_ROLLBACK_COUNTER && !MCUBOOT_VERSION_CHECK
#define MCUBOOT_ID_VERSION 16
#else
#define MCUBOOT_ID_VERSION 0
#endif
#if defined(MCUBOOT_SWAP_USING_SCRATCH)
#define MCUBOOT_ID_SCRATCH_ADDR MCUBOOT_SCRATCH_ADDR
#define MCUBOOT_ID_SCRATCH_SIZE MCUBOOT_SCRATCH_SIZE
#else
#define MCUBOOT_ID_SCRATCH_ADDR 0
#define MCUBOOT_ID_SCRATCH_SIZE 0
#endif
#if defined(MCUBOOT_INTENT_ADDR)
#define MCUBOOT_ID_INTENT_ADDR MCUBOOT_INTENT_ADDR
#else
#define MCUBOOT_ID_INTENT_ADDR 0
#endif
#if MCUBOOT_ID_MODE == 0 && MCUBOOT_ID_COUNTER == 0 && MCUBOOT_ID_VERSION == 0
#define MCUBOOT_ID_11 MCUBOOT_ID_10
#else
#define MCUBOOT_ID_11 MCUBOOT_ID_STEP(MCUBOOT_ID_STEP(MCUBOOT_ID_STEP(MCUBOOT_ID_STEP( \
    MCUBOOT_ID_10, MCUBOOT_ID_MODE + MCUBOOT_ID_COUNTER + MCUBOOT_ID_VERSION), \
    MCUBOOT_ID_SCRATCH_ADDR), MCUBOOT_ID_SCRATCH_SIZE), MCUBOOT_ID_INTENT_ADDR)
#endif
// The counter and shadow areas, when a board places them away from their defaults.
#if defined(MCUBOOT_SECCNT_FLASH) && MCUBOOT_SECCNT_ADDR != MCUBOOT_PRIMARY_END
#define MCUBOOT_ID_SECCNT_ADDR MCUBOOT_SECCNT_ADDR
#else
#define MCUBOOT_ID_SECCNT_ADDR 0
#endif
#if defined(MCUBOOT_ECC_SHADOW) && MCUBOOT_SHADOW_ADDR != MCUBOOT_AUX_AFTER_SECCNT
#define MCUBOOT_ID_SHADOW_ADDR MCUBOOT_SHADOW_ADDR
#else
#define MCUBOOT_ID_SHADOW_ADDR 0
#endif
#if MCUBOOT_ID_SECCNT_ADDR == 0 && MCUBOOT_ID_SHADOW_ADDR == 0
#define MCUBOOT_ID_12 MCUBOOT_ID_11
#else
#define MCUBOOT_ID_12 MCUBOOT_ID_STEP(MCUBOOT_ID_STEP(MCUBOOT_ID_11, MCUBOOT_ID_SECCNT_ADDR), MCUBOOT_ID_SHADOW_ADDR)
#endif
#define MCUBOOT_LAYOUT_ID MCUBOOT_ID_STEP(MCUBOOT_ID_12, MCUBOOT_API_VERSION)

#endif // MICROPY_INCLUDED_SHARED_MCUBOOT_MCUBOOT_LAYOUT_H
