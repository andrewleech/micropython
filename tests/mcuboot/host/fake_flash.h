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

#ifndef MICROPY_INCLUDED_TESTS_MCUBOOT_HOST_FAKE_FLASH_H
#define MICROPY_INCLUDED_TESTS_MCUBOOT_HOST_FAKE_FLASH_H

// In-memory flash devices with erase/write constraints, per-operation counting
// and power-cut injection, for host tests of the MCUboot glue.
//
// The device functions implement the flash part of the port interface of
// shared/mcuboot/include/mcuboot_port.h (mcuboot_port_flash_*, mcuboot_port_ecc_*) so the shared flash_map_backend.c
// can be linked against it unchanged. Offsets are device relative.
//
// Operation model
//   - Every call to mcuboot_port_flash_write() or mcuboot_port_flash_erase() is
//     one "operation" with a 1-based sequence number (the fault-injection
//     counter). Reads are not operations.
//   - write: offset and length must be multiples of the device write unit and
//     lie inside the device; otherwise -EINVAL and a violation is counted.
//   - erase: offset and length must be whole erase units and lie inside the device;
//     otherwise -EINVAL and a violation is counted. A device has one erase unit all over,
//     or runs of equal units one behind the other (fake_flash_dev_cfg_t.runs); a range may
//     cross from one run into the next.
//   - Programming only moves bits away from the erased value (AND for erased
//     0xFF, OR for erased 0x00). Programming a unit that already holds
//     non-erased data with different data is an "overprogram" event: the data
//     is combined as above and, on an ECC device, the unit becomes ECC-invalid
//     (the STM32H5 behaviour).
//   - ECC device: a unit torn by a cut inside a program or erase command is
//     ECC-invalid. mcuboot_port_flash_read() over an ECC-invalid unit fills the
//     destination with the stored data, bumps the ECC event count and returns
//     -EIO, as the raw port driver does after the NMI handler counted the event.
//     Erasing the sector clears the state.
//
// Power cut model (the "mode" numbering of the hardware fault injection word, see tests/mcuboot/fi_sweep.py)
//   FAKE_FLASH_CUT_BEFORE (0)  power lost before the operation has any effect
//   FAKE_FLASH_CUT_AFTER  (1)  power lost right after the operation completed
//   FAKE_FLASH_CUT_DURING (2)  power lost inside the operation; "tear" in 1/256
//                              of the operation selects how far it got:
//                              units before the point are done, the unit at the
//                              point is torn, later units are untouched.
//                              Erase: first part erased, one unit partly erased.
//                              Write: first units programmed, one unit torn.
//   FAKE_FLASH_CUT_BETWEEN (3) host-only: like DURING but the point falls exactly
//                              between two units, so no unit is torn (a sector
//                              with its first part erased, a chunk with its first
//                              units programmed). A one-unit write behaves like
//                              BEFORE.
//   FAKE_FLASH_CUT_WEAK   (4)  host-only: like DURING, but on an ECC device the torn unit is
//                              "corrected" instead of ECC-invalid: reads return rc 0, bump the
//                              corrected count (mcuboot_port_ecc_corrected) and give either the
//                              erased value (odd seed) or a random partial program (even seed),
//                              as the H563 does for a cut at the start of the program pulse.
//                              Programming over such a unit makes it ECC-invalid.
//   A device with tear_scatter set (a NOR flash, whose cells of an interrupted program or erase are
//   all in an undefined state) tears the unit at the point and every later unit of the command
//   in DURING and WEAK mode; BETWEEN is unchanged.
//   When the armed operation number is reached the partial effect is applied to the memory and the
//   cut handler is called; it does not return. The default handler exits the process with
//   FAKE_FLASH_CUT_EXIT, which suits a harness that runs each boot attempt in a forked child (all
//   flash state lives in shared anonymous memory and survives the child). An in-process harness
//   installs a handler that longjmp()s.

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#define FAKE_FLASH_MAX_DEVS (4)
#define FAKE_FLASH_MAX_RUNS (4)

// Exit status of the default cut handler.
#define FAKE_FLASH_CUT_EXIT (87)

typedef struct {
    uint32_t size;          // bytes
    uint32_t erase_unit;    // bytes, power of two; the unit of the whole device when n_runs is 0
    uint32_t n_runs;        // 0, or the number of runs[] entries: runs of equal erase units from offset 0
    struct {
        uint32_t size;      // bytes, a multiple of erase
        uint32_t erase;     // bytes, power of two
    } runs[FAKE_FLASH_MAX_RUNS];
    uint32_t write_unit;    // bytes, power of two, <= 32, divides every erase unit
    uint8_t erased_val;     // 0xff or 0x00
    bool ecc;               // torn or overprogrammed units become ECC-invalid
    bool atomic_program;    // a cut inside a program of a unit leaves it as it was or fully programmed
    bool tear_scatter;      // a cut inside a command tears every unit after the cut point, not just one
    uint32_t map_base;      // CPU address to place the memory at, 0 to place it anywhere
} fake_flash_dev_cfg_t;

typedef enum {
    FAKE_FLASH_CUT_BEFORE = 0,
    FAKE_FLASH_CUT_AFTER = 1,
    FAKE_FLASH_CUT_DURING = 2,
    FAKE_FLASH_CUT_BETWEEN = 3,
    FAKE_FLASH_CUT_WEAK = 4,
} fake_flash_cut_mode_t;

typedef enum {
    FAKE_FLASH_OP_WRITE = 0,
    FAKE_FLASH_OP_ERASE = 1,
} fake_flash_op_kind_t;

typedef struct {
    uint32_t seq;           // 1-based operation number since the last counter reset
    uint8_t kind;           // fake_flash_op_kind_t
    uint8_t dev;
    uint16_t flags;         // FAKE_FLASH_OPF_*
    uint32_t off;
    uint32_t len;
} fake_flash_op_t;

// Operation flags in fake_flash_op_t.flags.
#define FAKE_FLASH_OPF_CUT (1u << 0)        // this operation was the injected cut
#define FAKE_FLASH_OPF_ERROR (1u << 1)      // refused with an error, no effect

typedef struct {
    uint32_t writes;
    uint32_t erases;
    uint32_t reads;
    uint32_t dev_reads[FAKE_FLASH_MAX_DEVS];    // reads of each device
    uint32_t violations;    // refused calls (bad alignment or range)
    uint32_t overprograms;  // programs of a non-erased unit with different data
    uint32_t ecc_events;    // ECC-invalid units seen by reads
    uint32_t corrected_events; // corrected units seen by reads
    uint32_t trace_dropped; // operations beyond the trace capacity
} fake_flash_stats_t;

typedef struct fake_flash_snapshot fake_flash_snapshot_t;

// Create the devices. A device with map_base set is placed at that address, so code that reads a
// memory mapped flash through a pointer (bootutil with hash-in-place) sees the contents. Such reads
// bypass the ECC accounting, as they do on the hardware. All memory is MAP_SHARED anonymous, so a
// forked child's writes are visible to the parent. Devices start erased. Calling init again
// releases the previous devices. Returns 0 or a negative errno.
int fake_flash_init(unsigned n_devs, const fake_flash_dev_cfg_t *cfg);
void fake_flash_deinit(void);
unsigned fake_flash_dev_count(void);
const fake_flash_dev_cfg_t *fake_flash_dev_cfg(uint8_t dev);
// Erase unit at device offset off (0 outside the device).
uint32_t fake_flash_erase_at(uint8_t dev, uint32_t off);

// Direct access that bypasses counting, constraints and injection. poke marks
// units holding non-erased data as programmed and clears their ECC state.
void fake_flash_poke(uint8_t dev, uint32_t off, const void *src, uint32_t len);
void fake_flash_peek(uint8_t dev, uint32_t off, void *dst, uint32_t len);
void fake_flash_erase_raw(uint8_t dev, uint32_t off, uint32_t len);
// Puts the unit at off into the "corrected on read" state of FAKE_FLASH_CUT_WEAK. data gives its
// content (a partial program), NULL leaves it reading erased.
void fake_flash_set_weak(uint8_t dev, uint32_t off, const void *data);
const uint8_t *fake_flash_data(uint8_t dev);
bool fake_flash_unit_ecc_invalid(uint8_t dev, uint32_t off);
unsigned fake_flash_count_ecc_invalid(uint8_t dev, uint32_t off, uint32_t len);
unsigned fake_flash_count_corrected(uint8_t dev, uint32_t off, uint32_t len);

// Copy of all device contents and unit states (not counters, trace or fault
// state). restore() also zeroes the counter, statistics, ECC event count and
// trace, and disarms.
fake_flash_snapshot_t *fake_flash_snapshot(void);
void fake_flash_restore(const fake_flash_snapshot_t *s);
void fake_flash_snapshot_free(fake_flash_snapshot_t *s);

// Fault injection. target is the 1-based operation number at which the cut
// happens (0 disables). tear_q8 (0..255) is used by the DURING and BETWEEN modes.
// seed selects the pseudo-random bits of a torn unit.
void fake_flash_arm(uint32_t target, fake_flash_cut_mode_t mode, uint32_t tear_q8, uint32_t seed);
void fake_flash_disarm(void);
bool fake_flash_cut_fired(void);
typedef void (*fake_flash_cut_handler_t)(void *ctx);
void fake_flash_set_cut_handler(fake_flash_cut_handler_t fn, void *ctx);

// Operation counter, statistics and trace. The counter keeps running across
// simulated boots until reset, as the hardware counter does.
uint32_t fake_flash_op_count(void);
void fake_flash_counter_reset(void);
const fake_flash_stats_t *fake_flash_stats(void);
size_t fake_flash_trace_count(void);
const fake_flash_op_t *fake_flash_trace(void);

// Device access of the port interface without the port names, for a harness that dispatches
// devices itself (the SPI NOR model of spi_nor/ uses device 1 of the fake flash as its memory).
// Building with FAKE_FLASH_NO_PORT leaves mcuboot_port_flash_init/read/write/erase to the harness.
int fake_flash_dev_read(uint8_t dev, uint32_t off, void *dst, uint32_t len);
int fake_flash_dev_write(uint8_t dev, uint32_t off, const void *src, uint32_t len);
int fake_flash_dev_erase(uint8_t dev, uint32_t off, uint32_t len);
// Counts a refused call that the caller detected (a command a flash chip model rejects).
void fake_flash_note_violation(void);

// ---- port interface subset (shared/mcuboot/include/mcuboot_port.h) ----
int mcuboot_port_flash_init(void);
int mcuboot_port_flash_read(uint8_t dev, uint32_t off, void *dst, uint32_t len);
int mcuboot_port_flash_write(uint8_t dev, uint32_t off, const void *src, uint32_t len);
int mcuboot_port_flash_erase(uint8_t dev, uint32_t off, uint32_t len);
uint32_t mcuboot_port_ecc_events(void);
uint32_t mcuboot_port_ecc_corrected(void);
void mcuboot_port_ecc_clear(void);

#endif // MICROPY_INCLUDED_TESTS_MCUBOOT_HOST_FAKE_FLASH_H
