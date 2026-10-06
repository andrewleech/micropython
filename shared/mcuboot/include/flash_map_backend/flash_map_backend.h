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

#ifndef MICROPY_INCLUDED_SHARED_MCUBOOT_FLASH_MAP_BACKEND_FLASH_MAP_BACKEND_H
#define MICROPY_INCLUDED_SHARED_MCUBOOT_FLASH_MAP_BACKEND_FLASH_MAP_BACKEND_H

#include <stddef.h>
#include <stdint.h>
#include <sys/types.h>

// bootutil's flash abstraction, implemented in src/flash_map_backend.c over the port flash
// functions (mcuboot_port_flash_*) and the tables in flash_map.c.

struct flash_area {
    uint8_t fa_id;          // area id, see sysflash/sysflash.h
    uint8_t fa_device_id;   // index into mcuboot_devs[]
    uint16_t pad16;
    uint32_t fa_off;        // offset from the start of the device
    uint32_t fa_size;
};

struct flash_sector {
    uint32_t fs_off;        // offset from the start of the flash area
    uint32_t fs_size;
};

static inline uint8_t flash_area_get_id(const struct flash_area *fa) {
    return fa->fa_id;
}

static inline uint8_t flash_area_get_device_id(const struct flash_area *fa) {
    return fa->fa_device_id;
}

static inline uint32_t flash_area_get_off(const struct flash_area *fa) {
    return fa->fa_off;
}

static inline uint32_t flash_area_get_size(const struct flash_area *fa) {
    return fa->fa_size;
}

static inline uint32_t flash_sector_get_off(const struct flash_sector *fs) {
    return fs->fs_off;
}

static inline uint32_t flash_sector_get_size(const struct flash_sector *fs) {
    return fs->fs_size;
}

// Tables in flash_map.c.
extern const struct flash_area mcuboot_areas[];
extern const unsigned mcuboot_area_count;

// The area with the given id from mcuboot_areas[], or NULL. flash_area_open() is this lookup.
const struct flash_area *mcuboot_flash_area_find(uint8_t id);

// Erase unit of an area: the unit of the run of its device that the area lies in (an area lies in
// one run). 0 for a bad area and for the fsload stream area, which has no erase unit.
uint32_t mcuboot_area_erase(const struct flash_area *fa);

// ---- the surface bootutil references ----

// flash_area_open() returns pointers into mcuboot_areas[]; flash_area_close() does nothing.
int flash_area_open(uint8_t id, const struct flash_area **fa);
void flash_area_close(const struct flash_area *fa);

// Offsets are relative to the area. Every call is bounds checked against the area before the
// device is touched. An erase takes whole erase units of the area. Reads of the fsload stream
// device go to mcuboot_stream_area_read(); writes and erases of it fail with -EACCES.
int flash_area_read(const struct flash_area *fa, uint32_t off, void *dst, uint32_t len);
int flash_area_write(const struct flash_area *fa, uint32_t off, const void *src, uint32_t len);
int flash_area_erase(const struct flash_area *fa, uint32_t off, uint32_t len);

uint32_t flash_area_align(const struct flash_area *fa);
uint8_t flash_area_erased_val(const struct flash_area *fa);

int flash_area_get_sectors(int fa_id, uint32_t *count, struct flash_sector *sectors);
int flash_area_get_sector(const struct flash_area *fa, off_t off, struct flash_sector *fs);

int flash_area_id_from_multi_image_slot(int image_index, int slot);
int flash_area_id_from_image_slot(int slot);

// CPU/DFU address of offset 0 of a device.
int flash_device_base(uint8_t fd_id, uintptr_t *ret);

// ---- device level access for the bootloader front ends ----

// Same checks and flash policy as the flash_area_* calls but addressed by device and
// device-relative offset. Writes need offset and length to be multiples of the device write
// unit and erases multiples of the erase unit; otherwise -EINVAL. Every flash write outside
// bootutil (DFU, fsload, update log, counters) goes through these so that devices with
// ECC keep their trailer shadow words in step with the trailer sectors.
int mcuboot_flash_dev_read(uint8_t dev, uint32_t off, void *dst, uint32_t len);
int mcuboot_flash_dev_write(uint8_t dev, uint32_t off, const void *src, uint32_t len);
int mcuboot_flash_dev_erase(uint8_t dev, uint32_t off, uint32_t len);

// flash_area_read() for areas of records that carry their own CRC (update log, security
// counter): a read in which the controller corrected a word fails with -EIO like an invalid
// word does. The record is then not free (a cut during its program can leave a word that reads
// erased but takes no program) and not valid.
int mcuboot_flash_area_read_record(const struct flash_area *fa, uint32_t off, void *dst, uint32_t len);

// Checks the device and area tables against what the port accepts: power of two erase and write
// units, runs that add up to the device, areas inside their device, in one run and aligned to
// its erase unit, a read at both ends of each device, and for devices with ECC shadow words the
// shadow area layout. Returns 0 or a negative errno.
int mcuboot_flash_map_check(void);

// Bootloader only, after mcuboot_flash_map_check() and before boot_go(): completes missing
// shadow words and erases shadow sectors whose trailer sector is erased. Does nothing
// without MCUBOOT_ECC_SHADOW. Returns 0 or a negative errno.
int mcuboot_flash_scrub(void);

// Bootloader recovery: erases the trailer sectors of a slot (with their shadow sectors where the
// device has them), so that the slot holds no swap state. Used when boot_go() failed or
// asserted: a swap state that bootutil cannot interpret then stays in the primary slot trailer,
// which the DFU session never writes, and every boot after a good image has been installed would
// fail the same way. Returns 0 or a negative errno.
int mcuboot_flash_erase_trailer(uint8_t area_id);

// Area offset of the first trailer sector of a slot area: the slot loses MCUBOOT_TRAILER_SECTORS
// whole erase units at its end to its trailer, which is where bootutil places the trailer of
// either slot. The only slot of the single policy holds no swap state and has no trailer sectors:
// the offset is the area size. Returns 0, -ENOENT for a bad area or -EINVAL when the area is
// smaller than the trailer.
int mcuboot_flash_slot_trailer_off(const struct flash_area *fa, uint32_t *trailer_off);

// Read access to the fsload stream area (fa_device_id == MCUBOOT_DEV_STREAM); provided by
// fsload_stream.c when fsload is built.
int mcuboot_stream_area_read(uint32_t off, void *dst, uint32_t len);

#endif // MICROPY_INCLUDED_SHARED_MCUBOOT_FLASH_MAP_BACKEND_FLASH_MAP_BACKEND_H
