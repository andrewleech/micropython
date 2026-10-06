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

// mboot_region.c - region table and alt-setting descriptor generation.
//
// See mboot_region.h for the public API and design notes.

#include <errno.h>
#include <stddef.h>
#include <string.h>

#include "mboot_dfu.h"
#include "mboot_region.h"

// ---------------------------------------------------------------------------
// Internal constants
// ---------------------------------------------------------------------------

// MBOOT_MAX_ALT_SETTINGS is in mboot_region.h so mboot_usbd.c can
// _Static_assert against MBOOT_USBD_MAX_ALT_SETTINGS.

// Maximum number of regions per alt setting (folded group).
#define MBOOT_MAX_REGIONS_PER_ALT (8)

// Scratch buffer size for the ASCII interface string before UTF-16LE
// expansion: at most MBOOT_REGION_DESC_MAX_CONTENT_CHARS characters plus a
// NUL.
#define ALT_STR_ASCII_MAX (MBOOT_REGION_DESC_MAX_CONTENT_CHARS + 1)

// ---------------------------------------------------------------------------
// Alt-setting group table
// ---------------------------------------------------------------------------

// One alt-setting group: a run of adjacent (same-name, contiguous-address)
// mboot_region_t entries, stored as [first, first+count) indices into
// s_regions[].
typedef struct {
    uint8_t first;  // index of first region in s_regions[]
    uint8_t count;  // number of regions in this group
} mboot_alt_group_t;

static mboot_alt_group_t s_groups[MBOOT_MAX_ALT_SETTINGS];
static uint8_t s_num_alts;
static uint8_t s_active_alt;
static bool s_initialised;

// Cached region table, filled once by mboot_region_init() from
// mboot_port_get_regions().  Everything after that uses these variables and
// does not call the port again.
static const mboot_region_t *s_regions;
static size_t s_regions_count;

// ---------------------------------------------------------------------------
// Internal helpers
// ---------------------------------------------------------------------------

// strcmp-like compare; NULL is never equal to anything.
static bool names_equal(const char *a, const char *b) {
    if (a == NULL || b == NULL) {
        return false;
    }
    return strcmp(a, b) == 0;
}

// Return the alt group for alt, or NULL if out of range.
static const mboot_alt_group_t *get_group(uint8_t alt) {
    if (alt >= s_num_alts) {
        return NULL;
    }
    return &s_groups[alt];
}

// Lowest address in an alt group.
static mboot_addr_t group_base(const mboot_alt_group_t *g) {
    // Regions are in address order, so the first has the lowest address.
    return s_regions[g->first].addr;
}

// Highest address (exclusive) in an alt group.
static mboot_addr_t group_end(const mboot_alt_group_t *g) {
    const mboot_region_t *last = &s_regions[g->first + g->count - 1];
    return last->addr + last->size;
}

// Choose the dfu-util unit character and scale factor for a sector size:
// 'M' (MiB) if it divides evenly, else 'K' (KiB), else 'B' (bytes).
// scaled_size gets the sector_size in the chosen unit.
static char choose_unit(uint32_t sector_size, uint32_t *scaled_size) {
    if ((sector_size % (1024u * 1024u)) == 0) {
        *scaled_size = sector_size / (1024u * 1024u);
        return 'M';
    } else if ((sector_size % 1024u) == 0) {
        *scaled_size = sector_size / 1024u;
        return 'K';
    } else {
        *scaled_size = sector_size;
        return 'B';
    }
}

// Derive the DfuSe permission character from a region's flags.
//
// The DfuSe memory layout string (ST UM0424, parsed by dfu-util as
// memtype = character & 7) encodes access permissions as a character from
// 'a' to 'g': 'a' + (bits - 1) where
//   bit 0 (value 1): readable
//   bit 1 (value 2): erasable
//   bit 2 (value 4): writable
// So 0x60 + bits gives 'a' for 1 up to 'g' for 7.
//
// MBOOT_REGION_FLAG_READABLE                    -> bit 0
// MBOOT_REGION_FLAG_ERASE_REQUIRED_BEFORE_WRITE -> bits 1+2 (erase+write)
// MBOOT_REGION_FLAG_READ_ONLY                   -> clears bits 1+2
//
// Resulting map:
//   READABLE only             -> 'a' (1, read-only)
//   ERASE_REQUIRED only       -> 'f' (6, write+erase, no read)
//   READABLE + ERASE_REQUIRED -> 'g' (7, read+write+erase)
//   READ_ONLY with anything   -> 'a' (never erasable or writable)
//   no access at all          -> '`' (0, no valid DfuSe character exists)
static char region_perm_char(uint8_t flags) {
    uint8_t perm = 0;
    if (flags & MBOOT_REGION_FLAG_READABLE) {
        perm |= 1u; // readable
    }
    if ((flags & MBOOT_REGION_FLAG_ERASE_REQUIRED_BEFORE_WRITE) &&
        !(flags & MBOOT_REGION_FLAG_READ_ONLY)) {
        perm |= 6u; // erasable + writable
    }
    return (char)(0x60 + perm);
}

// Integer formatter that does not use snprintf: write the decimal digits of v
// into buf at buf[*pos], advancing *pos.  buf must be large enough.
static void write_uint32(char *buf, size_t buf_size, size_t *pos, uint32_t v) {
    // Write digits in reverse, then flip.
    char tmp[12];
    int n = 0;
    if (v == 0) {
        tmp[n++] = '0';
    } else {
        while (v > 0) {
            tmp[n++] = (char)('0' + (v % 10));
            v /= 10;
        }
    }
    // Write reversed into buf.
    for (int i = n - 1; i >= 0 && *pos < buf_size - 1; --i) {
        buf[(*pos)++] = tmp[i];
    }
}

// Write a lowercase hex digit.
static char hex_nibble(unsigned int v) {
    return (char)(v < 10 ? '0' + v : 'a' + (v - 10));
}

// Write mboot_addr_t as 0x%08X into buf.
static void write_addr(char *buf, size_t buf_size, size_t *pos, mboot_addr_t addr) {
    if (*pos + 2 >= buf_size) {
        return;
    }
    buf[(*pos)++] = '0';
    buf[(*pos)++] = 'x';
    for (int i = 7; i >= 0 && *pos < buf_size - 1; --i) {
        buf[(*pos)++] = hex_nibble((unsigned int)((addr >> (i * 4)) & 0xFu));
    }
}

// Write a single char to buf.
static void write_char(char *buf, size_t buf_size, size_t *pos, char c) {
    if (*pos < buf_size - 1) {
        buf[(*pos)++] = c;
    }
}

// Write a NUL-terminated string to buf.
static void write_str(char *buf, size_t buf_size, size_t *pos, const char *s) {
    while (*s && *pos < buf_size - 1) {
        buf[(*pos)++] = *s++;
    }
}

// ---------------------------------------------------------------------------
// mboot_region_init
// ---------------------------------------------------------------------------

int mboot_region_init(void) {
    s_num_alts = 0;
    s_active_alt = 0;
    s_initialised = false;
    s_regions = NULL;
    s_regions_count = 0;

    // Fetch the port's region table once and cache the pointer and count for
    // this init and everything after it.
    mboot_port_get_regions(&s_regions, &s_regions_count);

    if (s_regions_count == 0 || s_regions == NULL) {
        return -EINVAL;
    }

    // Sector total of the group being built.  The touched-sector bitmap in
    // mboot_dfu.c has one bit per sector of the active alt setting, so a group
    // must fit in it in total.
    uint32_t group_sectors = 0;

    // Walk the region table, folding adjacent same-name contiguous regions into
    // one group.
    for (size_t i = 0; i < s_regions_count; ++i) {
        const mboot_region_t *r = &s_regions[i];

        // Verify sector_count * sector_size == size.
        if ((mboot_addr_t)r->sector_count * r->sector_size != r->size) {
            return -EINVAL;
        }

        // Fail on a sector count beyond the touched-sector bitmap rather than
        // have the bitmap silently stop tracking past the limit and re-erase
        // sectors mid-transfer.  Bounding each region first keeps the group
        // total below from overflowing.
        if (r->sector_count > MBOOT_DFU_MAX_SECTOR_COUNT) {
            return -EINVAL;
        }

        // Regions of one alt setting (same name as the previous region) must be
        // contiguous and ascending so the folded interface string has the right
        // base address.  Regions with different names form separate alt
        // settings and can be in any address order, but must not overlap an
        // earlier region.
        if (i > 0) {
            const mboot_region_t *prev = &s_regions[i - 1];
            if (names_equal(prev->name, r->name)) {
                if (r->addr != prev->addr + prev->size) {
                    return -EINVAL;
                }
            }
            for (size_t j = 0; j < i; ++j) {
                const mboot_region_t *o = &s_regions[j];
                if (r->addr < o->addr + o->size && o->addr < r->addr + r->size) {
                    return -EINVAL;
                }
            }
        }

        // Ask the port.  A read-only region is never written, so it is not
        // asked about.
        if (!(r->flags & MBOOT_REGION_FLAG_READ_ONLY) &&
            !mboot_port_flash_is_writable(r->addr, (size_t)r->size)) {
            return -EINVAL;
        }

        // Append to the current group if the region has the group's name (the
        // check above has then made it contiguous with the preceding region,
        // which is in the group) and the group is not full.  A region with a
        // different name starts a new alt setting, wherever it lies in the
        // address space.
        if (s_num_alts > 0 &&
            names_equal(s_regions[s_groups[s_num_alts - 1].first].name, r->name) &&
            s_groups[s_num_alts - 1].count < MBOOT_MAX_REGIONS_PER_ALT) {
            // Append to existing group.
            if (group_sectors + r->sector_count > MBOOT_DFU_MAX_SECTOR_COUNT) {
                return -EINVAL;
            }
            group_sectors += r->sector_count;
            s_groups[s_num_alts - 1].count++;
        } else {
            // Start a new group.
            if (s_num_alts >= MBOOT_MAX_ALT_SETTINGS) {
                return -EINVAL;
            }
            s_groups[s_num_alts].first = (uint8_t)i;
            s_groups[s_num_alts].count = 1;
            group_sectors = r->sector_count;
            s_num_alts++;
        }
    }

    s_initialised = true;
    return 0;
}

// ---------------------------------------------------------------------------
// mboot_region_count
// ---------------------------------------------------------------------------

size_t mboot_region_count(void) {
    return (size_t)s_num_alts;
}

// ---------------------------------------------------------------------------
// mboot_region_get_alt_string
// ---------------------------------------------------------------------------

int mboot_region_get_alt_string(uint8_t alt, uint16_t *out_utf16, size_t out_max_chars) {
    const mboot_alt_group_t *g = get_group(alt);
    if (g == NULL) {
        return -1;
    }

    // Build the ASCII string in a scratch buffer.  ALT_STR_ASCII_MAX is
    // MBOOT_REGION_DESC_MAX_CONTENT_CHARS + 1, so it holds the maximum content
    // plus the NUL.
    char ascii[ALT_STR_ASCII_MAX];
    size_t pos = 0;

    // "@<name> /<base_addr>/"
    write_char(ascii, sizeof(ascii), &pos, '@');
    write_str(ascii, sizeof(ascii), &pos, s_regions[g->first].name);
    write_char(ascii, sizeof(ascii), &pos, ' ');
    write_char(ascii, sizeof(ascii), &pos, '/');
    write_addr(ascii, sizeof(ascii), &pos, group_base(g));
    write_char(ascii, sizeof(ascii), &pos, '/');

    // Comma-joined geometry runs, one per region in the group.
    for (uint8_t i = 0; i < g->count; ++i) {
        const mboot_region_t *r = &s_regions[g->first + i];

        if (i > 0) {
            write_char(ascii, sizeof(ascii), &pos, ',');
        }

        // <count>*<size><unit><perm>
        uint32_t scaled = 0;
        char unit = choose_unit(r->sector_size, &scaled);
        char perm = region_perm_char(r->flags);

        write_uint32(ascii, sizeof(ascii), &pos, r->sector_count);
        write_char(ascii, sizeof(ascii), &pos, '*');
        // dfu-util expects a 3-digit zero-padded size for K and M, plain for B.
        if (unit == 'K' || unit == 'M') {
            // Zero-pad to 3 digits.
            if (scaled < 100) {
                write_char(ascii, sizeof(ascii), &pos, '0');
            }
            if (scaled < 10) {
                write_char(ascii, sizeof(ascii), &pos, '0');
            }
        }
        write_uint32(ascii, sizeof(ascii), &pos, scaled);
        write_char(ascii, sizeof(ascii), &pos, unit);
        write_char(ascii, sizeof(ascii), &pos, perm);
    }

    ascii[pos] = '\0';

    // Number of UTF-16 code units for the content (1:1 for ASCII).
    size_t content_chars = pos;

    // USB string descriptor content limit: the total descriptor length must fit
    // in one byte (max 255), so content is at most (255 - 2) / 2 = 126 UTF-16
    // code units.  A longer string means the region table has a name that is
    // too long or too many geometry groups.
    if (content_chars > MBOOT_REGION_DESC_MAX_CONTENT_CHARS) {
        return -1;
    }

    if (content_chars > out_max_chars) {
        content_chars = out_max_chars;
    }

    // Descriptor header: length byte and type byte 0x03.  The total length is
    // 2 + content_chars * 2, which fits in uint8_t because content_chars <= 126.
    uint8_t *hdr = (uint8_t *)out_utf16;
    hdr[0] = (uint8_t)(2 + content_chars * 2);
    hdr[1] = 0x03;

    // Expand ASCII to UTF-16LE: each byte becomes [byte, 0x00].
    uint16_t *dst = out_utf16 + 1; // skip the header uint16
    for (size_t i = 0; i < content_chars; ++i) {
        dst[i] = (uint16_t)(unsigned char)ascii[i];
    }

    return (int)content_chars;
}

// ---------------------------------------------------------------------------
// Active alt-setting tracking
// ---------------------------------------------------------------------------

int mboot_region_set_active(uint8_t alt) {
    if (alt >= s_num_alts) {
        // Out of range: leave the active alt alone and return an error so the
        // USB SET_INTERFACE handler can STALL the request.
        return -EINVAL;
    }
    s_active_alt = alt;
    return 0;
}

uint8_t mboot_region_get_active(void) {
    return s_active_alt;
}

mboot_addr_t mboot_region_active_base(void) {
    const mboot_alt_group_t *g = get_group(s_active_alt);
    if (g == NULL) {
        return 0;
    }
    return group_base(g);
}

mboot_addr_t mboot_region_active_size(void) {
    const mboot_alt_group_t *g = get_group(s_active_alt);
    if (g == NULL) {
        return 0;
    }
    return group_end(g) - group_base(g);
}

// ---------------------------------------------------------------------------
// Address dispatch helpers
// ---------------------------------------------------------------------------

// Check that [addr, addr+len) lies within the active group.  Returns 0 if so,
// -ERANGE otherwise.
static int check_range_active(mboot_addr_t addr, size_t len) {
    const mboot_alt_group_t *g = get_group(s_active_alt);
    if (g == NULL) {
        return -ERANGE;
    }
    mboot_addr_t base = group_base(g);
    mboot_addr_t end = group_end(g);
    // Overflow guard: if addr > end the subtraction wraps.
    if (addr < base || (mboot_addr_t)len > end - base || addr > end - (mboot_addr_t)len) {
        return -ERANGE;
    }
    return 0;
}

// ---------------------------------------------------------------------------
// mboot_region_for_address
// ---------------------------------------------------------------------------

static const mboot_region_t *mboot_region_for_address(mboot_addr_t addr) {
    for (size_t i = 0; i < s_regions_count; ++i) {
        const mboot_region_t *r = &s_regions[i];
        if (addr >= r->addr && addr < r->addr + r->size) {
            return r;
        }
    }
    return NULL;
}

// ---------------------------------------------------------------------------
// mboot_region_sector_index
// ---------------------------------------------------------------------------

bool mboot_region_sector_index(uint8_t alt, mboot_addr_t addr, uint32_t *out_idx) {
    const mboot_alt_group_t *g = get_group(alt);
    if (g == NULL) {
        return false;
    }
    uint32_t before = 0;
    for (uint8_t i = 0; i < g->count; ++i) {
        const mboot_region_t *r = &s_regions[g->first + i];
        if (addr >= r->addr && addr - r->addr < r->size) {
            *out_idx = before + (uint32_t)((addr - r->addr) / r->sector_size);
            return true;
        }
        before += r->sector_count;
    }
    return false;
}

// ---------------------------------------------------------------------------
// Read-only regions
// ---------------------------------------------------------------------------

// True if any region of the active group that overlaps [addr, addr+len) is
// read-only.  The range must already be inside the active group.
static bool range_touches_read_only(mboot_addr_t addr, size_t len) {
    const mboot_alt_group_t *g = get_group(s_active_alt);
    mboot_addr_t last = addr + (mboot_addr_t)(len > 0 ? len - 1 : 0);
    for (uint8_t i = 0; i < g->count; ++i) {
        const mboot_region_t *r = &s_regions[g->first + i];
        if ((r->flags & MBOOT_REGION_FLAG_READ_ONLY) &&
            addr < r->addr + r->size && last >= r->addr) {
            return true;
        }
    }
    return false;
}

bool mboot_region_alt_is_read_only(uint8_t alt) {
    const mboot_alt_group_t *g = get_group(alt);
    if (g == NULL) {
        return false;
    }
    for (uint8_t i = 0; i < g->count; ++i) {
        if (!(s_regions[g->first + i].flags & MBOOT_REGION_FLAG_READ_ONLY)) {
            return false;
        }
    }
    return true;
}

// ---------------------------------------------------------------------------
// mboot_region_write
// ---------------------------------------------------------------------------

int mboot_region_write(mboot_addr_t addr, const uint8_t *src, size_t len) {
    int rc = check_range_active(addr, len);
    if (rc != 0) {
        return rc;
    }
    if (range_touches_read_only(addr, len)) {
        return -EACCES;
    }
    return mboot_port_flash_write(addr, src, len);
}

// ---------------------------------------------------------------------------
// mboot_region_erase_page
// ---------------------------------------------------------------------------

int mboot_region_erase_page(mboot_addr_t addr, mboot_addr_t *next_addr) {
    const mboot_alt_group_t *g = get_group(s_active_alt);
    if (g == NULL) {
        return -ERANGE;
    }
    mboot_addr_t base = group_base(g);
    mboot_addr_t end = group_end(g);
    if (addr < base || addr >= end) {
        return -ERANGE;
    }
    if (range_touches_read_only(addr, 1)) {
        return -EACCES;
    }
    return mboot_port_flash_page_erase(addr, next_addr);
}

// ---------------------------------------------------------------------------
// mboot_region_read
// ---------------------------------------------------------------------------

int mboot_region_read(mboot_addr_t addr, uint8_t *dst, size_t len) {
    int rc = check_range_active(addr, len);
    if (rc != 0) {
        return rc;
    }
    // Find the region of the active group that contains addr and check its
    // readable flag.  check_range_active has already confirmed addr is inside
    // the active group, so mboot_region_for_address finds one of the group's
    // regions.
    const mboot_region_t *r = mboot_region_for_address(addr);
    if (r == NULL || !(r->flags & MBOOT_REGION_FLAG_READABLE)) {
        return -EACCES;
    }
    return mboot_port_flash_read(addr, dst, len);
}

// ---------------------------------------------------------------------------
// mboot_region_sector_iter
// ---------------------------------------------------------------------------

bool mboot_region_sector_iter(uint8_t alt, uint32_t *cookie,
    mboot_addr_t *out_addr, uint32_t *out_size) {
    const mboot_alt_group_t *g = get_group(alt);
    if (g == NULL) {
        return false;
    }

    // The cookie is the sector index counted across all regions in the group.
    // Walk the regions to find which one owns this sector.
    uint32_t sector_idx = *cookie;

    for (uint8_t i = 0; i < g->count; ++i) {
        const mboot_region_t *r = &s_regions[g->first + i];
        if (sector_idx < r->sector_count) {
            *out_addr = r->addr + (mboot_addr_t)sector_idx * r->sector_size;
            *out_size = r->sector_size;
            *cookie = *cookie + 1;
            return true;
        }
        sector_idx -= r->sector_count;
    }

    // All sectors exhausted.
    return false;
}
