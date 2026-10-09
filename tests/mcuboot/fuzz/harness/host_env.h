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

#ifndef MICROPY_INCLUDED_TESTS_MCUBOOT_FUZZ_HARNESS_HOST_ENV_H
#define MICROPY_INCLUDED_TESTS_MCUBOOT_FUZZ_HARNESS_HOST_ENV_H

// Host stand-in for the per-port flash and the generated flash map, for the fsload tests and
// fuzz targets. The real shared/mcuboot/src/flash_map_backend.c is linked on top of it.
//
//   device 0 "fs"     base 0x90000000, 8 MiB, read only; its contents are a caller supplied
//                     buffer, bytes beyond it read as the erased value 0xFF
//   device 1 "slots"  base 0x08000000, 1 MiB RAM, 4 KiB erase unit, 16 byte write unit
//
//   area 1 primary    dev 1 0x10000, 128 KiB
//   area 2 secondary  dev 1 0x30000, 132 KiB (primary plus one erase unit); with
//                     FUZZ_POLICY_SINGLE the secondary id maps to the primary area
//   area 7 intent     dev 1 0x53000, 4 KiB
//
// Writes follow the flash model of the port interface: the target bytes must be erased (a second
// program of a non-erased byte counts as a violation) and the write unit alignment must hold.

#include <stddef.h>
#include <stdint.h>

#define HOST_FS_BASE 0x90000000u
#define HOST_FS_SIZE (8u << 20)
#define HOST_SLOT_BASE 0x08000000u
#define HOST_SLOT_SIZE (1u << 20)
#define HOST_ERASE 4096u

#define HOST_PRIMARY_OFF 0x10000u
#define HOST_PRIMARY_SIZE 0x20000u
#define HOST_SECONDARY_OFF 0x30000u
#define HOST_SECONDARY_SIZE 0x21000u
#define HOST_INTENT_OFF 0x53000u
#define HOST_INTENT_SIZE 0x1000u

typedef struct {
    uint32_t writes;            // calls of mcuboot_port_flash_write on device 1
    uint32_t erases;            // calls of mcuboot_port_flash_erase on device 1
    uint32_t violations;        // bad alignment, write to non-erased bytes, access to device 0
    uint32_t fs_reads;          // calls of mcuboot_port_flash_read on device 0
    uint64_t fs_read_bytes;     // bytes read from device 0
    uint32_t cut_ops;           // write and erase calls refused after the power cut
} host_flash_stats_t;

// Sets the contents of device 0: data[0..len) followed by erased bytes. The buffer must stay
// valid while the device is read.
void host_fs_set(const uint8_t *data, size_t len);

// Erases device 1 and clears the statistics.
void host_slots_reset(void);
uint8_t *host_slots(void);

const host_flash_stats_t *host_flash_stats(void);
void host_flash_stats_clear(void);

// Power cut model: the first n write and erase calls after this one take effect, every later
// call fails with -EIO and changes nothing, until host_flash_power_on(). The call counter
// restarts at zero. host_flash_ops() is the number of calls since the last restart, refused
// ones included.
void host_flash_cut_after(uint32_t n);
void host_flash_power_on(void);
uint32_t host_flash_ops(void);

// The n-th (1 based) read of device 0 after this call returns its first byte inverted; 0 turns
// it off. Models a source that does not read back the same in pass 2 of a load.
void host_fs_corrupt_read(uint32_t n);

#endif // MICROPY_INCLUDED_TESTS_MCUBOOT_FUZZ_HARNESS_HOST_ENV_H
