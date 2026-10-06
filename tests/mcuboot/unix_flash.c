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

// Unix port backend for the MCUboot application glue: the per-port functions of the app role
// (mcuboot_port_flash_*, retention word, request RAM, reset) over a file, plus the "mcuboot_fake"
// module that lets a test script inspect and manipulate the fake device.
//
// The devices of the flash map (mcuboot_devs[]) are stored one after the other in a
// single file. The file is named by the environment variable MCUBOOT_UNIX_FLASH; without it an
// unlinked temporary file is used, so every process starts with erased flash.
//
// mcuboot_port_reset() does not terminate the process: it raises SystemExit, which a test
// script catches to continue and inspect the state the "bootloader" would see.

#include <errno.h>
#include <fcntl.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#include "py/runtime.h"
#include "py/mperrno.h"
#include "py/objstr.h"

#include "extmod/modmcuboot.h"

// The bootutil configuration redefines assert() for the bootloader.
#undef assert

#include "mcuboot_config/mcuboot_config.h"
#include "sysflash/sysflash.h"
#include "flash_map_backend/flash_map_backend.h"
#include "bootutil/bootutil_public.h"
#include "bootutil_priv.h"
#include "mcuboot_request.h"

#define MAX_DEVS        4
#define COPY_CHUNK      512

enum {
    OP_READ = 0,
    OP_WRITE = 1,
    OP_ERASE = 2,
};

static int flash_fd = -1;
static off_t dev_file_off[MAX_DEVS];

static unsigned long count_ops[3];
static unsigned long count_overprogram;
static int inject_op = -1;
static unsigned long inject_after;

static uint64_t request_ram[MCUBOOT_REQ_REGION_SIZE / sizeof(uint64_t)];
static uint32_t retention_word;
static unsigned long reset_count;

// ---- backing file ----

static int fill_erased(off_t start, off_t len, uint8_t val) {
    uint8_t buf[4096];
    memset(buf, val, sizeof(buf));
    while (len > 0) {
        size_t n = len < (off_t)sizeof(buf) ? (size_t)len : sizeof(buf);
        if (pwrite(flash_fd, buf, n, start) != (ssize_t)n) {
            return -EIO;
        }
        start += n;
        len -= n;
    }
    return 0;
}

static int fake_init(void) {
    if (flash_fd >= 0) {
        return 0;
    }
    if (mcuboot_dev_count > MAX_DEVS) {
        return -ENODEV;
    }
    off_t total = 0;
    for (unsigned i = 0; i < mcuboot_dev_count; ++i) {
        dev_file_off[i] = total;
        total += mcuboot_devs[i].size;
    }

    bool fresh = true;
    const char *path = getenv("MCUBOOT_UNIX_FLASH");
    if (path != NULL && path[0] != '\0') {
        flash_fd = open(path, O_RDWR | O_CREAT, 0600);
        if (flash_fd >= 0) {
            fresh = lseek(flash_fd, 0, SEEK_END) != total;
        }
    } else {
        char tmpl[] = "/tmp/mcuboot_flash_XXXXXX";
        flash_fd = mkstemp(tmpl);
        if (flash_fd >= 0) {
            unlink(tmpl);
        }
    }
    if (flash_fd < 0) {
        return -EIO;
    }
    if (fresh) {
        int rc = ftruncate(flash_fd, total) == 0 ? 0 : -EIO;
        for (unsigned i = 0; rc == 0 && i < mcuboot_dev_count; ++i) {
            rc = fill_erased(dev_file_off[i], mcuboot_devs[i].size, mcuboot_devs[i].erased_val);
        }
        if (rc != 0) {
            close(flash_fd);
            flash_fd = -1;
            return rc;
        }
    }
    return 0;
}

static bool range_ok(uint8_t dev, uint32_t off, uint32_t len) {
    return dev < mcuboot_dev_count && off <= mcuboot_devs[dev].size && len <= mcuboot_devs[dev].size - off;
}

// Returns true if this operation is the one selected for a forced failure.
static bool inject_hit(int op) {
    if (inject_op != op) {
        return false;
    }
    if (--inject_after > 0) {
        return false;
    }
    inject_op = -1;
    return true;
}

// ---- raw access without constraint checks or counters ----

static int raw_read(uint8_t dev, uint32_t off, void *dst, uint32_t len) {
    int rc = fake_init();
    if (rc != 0) {
        return rc;
    }
    return pread(flash_fd, dst, len, dev_file_off[dev] + off) == (ssize_t)len ? 0 : -EIO;
}

// Programming only moves bits away from the erased value, like NOR flash. *overprogram counts
// the bytes whose requested value could not be reached because a bit would have to be set.
static int raw_program(uint8_t dev, uint32_t off, const uint8_t *src, uint32_t len, unsigned long *overprogram) {
    uint8_t erased = mcuboot_devs[dev].erased_val;
    uint8_t cur[COPY_CHUNK];
    uint8_t out[COPY_CHUNK];
    while (len > 0) {
        uint32_t n = len < COPY_CHUNK ? len : COPY_CHUNK;
        int rc = raw_read(dev, off, cur, n);
        if (rc != 0) {
            return rc;
        }
        for (uint32_t i = 0; i < n; ++i) {
            out[i] = erased == 0xff ? (cur[i] & src[i]) : (cur[i] | src[i]);
            if (out[i] != src[i]) {
                ++*overprogram;
            }
        }
        if (pwrite(flash_fd, out, n, dev_file_off[dev] + off) != (ssize_t)n) {
            return -EIO;
        }
        off += n;
        src += n;
        len -= n;
    }
    return 0;
}

static int raw_erase(uint8_t dev, uint32_t off, uint32_t len) {
    int rc = fake_init();
    if (rc != 0) {
        return rc;
    }
    return fill_erased(dev_file_off[dev] + off, len, mcuboot_devs[dev].erased_val);
}

// ---- mcuboot_port_* for the app role ----

int mcuboot_port_flash_init(void) {
    return fake_init();
}

int mcuboot_port_flash_read(uint8_t dev, uint32_t off, void *dst, uint32_t len) {
    if (!range_ok(dev, off, len)) {
        return -EINVAL;
    }
    ++count_ops[OP_READ];
    if (inject_hit(OP_READ)) {
        return -EIO;
    }
    return raw_read(dev, off, dst, len);
}

int mcuboot_port_flash_write(uint8_t dev, uint32_t off, const void *src, uint32_t len) {
    if (!range_ok(dev, off, len) || off % mcuboot_devs[dev].write_unit != 0 || len % mcuboot_devs[dev].write_unit != 0) {
        return -EINVAL;
    }
    ++count_ops[OP_WRITE];
    if (inject_hit(OP_WRITE)) {
        return -EIO;
    }
    int rc = fake_init();
    return rc != 0 ? rc : raw_program(dev, off, src, len, &count_overprogram);
}

int mcuboot_port_flash_erase(uint8_t dev, uint32_t off, uint32_t len) {
    if (!range_ok(dev, off, len) || !mcuboot_dev_erase_range_ok(&mcuboot_devs[dev], off, len)) {
        return -EINVAL;
    }
    ++count_ops[OP_ERASE];
    if (inject_hit(OP_ERASE)) {
        return -EIO;
    }
    return raw_erase(dev, off, len);
}

void mcuboot_port_wdt_feed(void) {
}

void mcuboot_port_log_write(const char *s, size_t n) {
    (void)s;
    (void)n;
}

uint32_t mcuboot_port_retention_read(void) {
    return retention_word;
}

void mcuboot_port_retention_write(uint32_t v) {
    retention_word = v;
}

void *mcuboot_port_request_ram(size_t *size_out) {
    *size_out = sizeof(request_ram);
    return request_ram;
}

MCUBOOT_NORETURN void mcuboot_port_reset(void) {
    ++reset_count;
    mp_raise_type_arg(&mp_type_SystemExit, MP_OBJ_NEW_SMALL_INT(0));
}

// ---- mcuboot_fake module ----

static void check_errno(int rc) {
    if (rc != 0) {
        mp_raise_OSError(-rc);
    }
}

static uint8_t dev_of_addr(mp_obj_t addr_in, uint32_t len, uint32_t *off) {
    uint32_t addr = mp_obj_get_uint(addr_in);
    for (unsigned i = 0; i < mcuboot_dev_count; ++i) {
        const mcuboot_flash_dev_t *d = &mcuboot_devs[i];
        if (addr >= d->base && addr - d->base <= d->size && len <= d->size - (addr - d->base)) {
            *off = addr - d->base;
            return i;
        }
    }
    mp_raise_ValueError(MP_ERROR_TEXT("address range outside the devices"));
}

// geometry() -> (base, size, erase_unit, write_unit, erased_val, max_image_size) of device 0; the erase
// unit is the one at the start of the device, the unix variants have one unit all over
static mp_obj_t fake_geometry(void) {
    const mcuboot_flash_dev_t *d = &mcuboot_devs[0];
    mp_obj_t items[6] = {
        mp_obj_new_int_from_uint(d->base),
        mp_obj_new_int_from_uint(d->size),
        mp_obj_new_int_from_uint(mcuboot_dev_erase_at(d, 0)),
        MP_OBJ_NEW_SMALL_INT(d->write_unit),
        MP_OBJ_NEW_SMALL_INT(d->erased_val),
        mp_obj_new_int_from_uint(MCUBOOT_MAX_IMAGE_SIZE),
    };
    return mp_obj_new_tuple(6, items);
}
static MP_DEFINE_CONST_FUN_OBJ_0(fake_geometry_obj, fake_geometry);

// erase_all(): every device back to the erased state; counters and injection are cleared.
static mp_obj_t fake_erase_all(void) {
    for (unsigned i = 0; i < mcuboot_dev_count; ++i) {
        check_errno(raw_erase(i, 0, mcuboot_devs[i].size));
    }
    memset(count_ops, 0, sizeof(count_ops));
    count_overprogram = 0;
    inject_op = -1;
    memset(request_ram, 0, sizeof(request_ram));
    retention_word = 0;
    reset_count = 0;
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_0(fake_erase_all_obj, fake_erase_all);

// read(addr, n) -> bytes; addr is a CPU/DFU address as in mcuboot.slots()
static mp_obj_t fake_read(mp_obj_t addr_in, mp_obj_t len_in) {
    mp_uint_t len = mp_obj_get_uint(len_in);
    uint32_t off;
    uint8_t dev = dev_of_addr(addr_in, len, &off);
    vstr_t vstr;
    vstr_init_len(&vstr, len);
    check_errno(raw_read(dev, off, vstr.buf, len));
    return mp_obj_new_bytes_from_vstr(&vstr);
}
static MP_DEFINE_CONST_FUN_OBJ_2(fake_read_obj, fake_read);

// write(addr, data): program without alignment checks or counters (bits can only be cleared)
static mp_obj_t fake_write(mp_obj_t addr_in, mp_obj_t data_in) {
    mp_buffer_info_t bufinfo;
    mp_get_buffer_raise(data_in, &bufinfo, MP_BUFFER_READ);
    uint32_t off;
    uint8_t dev = dev_of_addr(addr_in, bufinfo.len, &off);
    unsigned long over = 0;
    check_errno(raw_program(dev, off, bufinfo.buf, bufinfo.len, &over));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_2(fake_write_obj, fake_write);

// erase(addr, n): erase without alignment checks or counters
static mp_obj_t fake_erase(mp_obj_t addr_in, mp_obj_t len_in) {
    mp_uint_t len = mp_obj_get_uint(len_in);
    uint32_t off;
    uint8_t dev = dev_of_addr(addr_in, len, &off);
    check_errno(raw_erase(dev, off, len));
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_2(fake_erase_obj, fake_erase);

// counters() -> (reads, writes, erases, overprograms) since erase_all() or clear_counters()
static mp_obj_t fake_counters(void) {
    mp_obj_t items[4] = {
        mp_obj_new_int_from_uint(count_ops[OP_READ]),
        mp_obj_new_int_from_uint(count_ops[OP_WRITE]),
        mp_obj_new_int_from_uint(count_ops[OP_ERASE]),
        mp_obj_new_int_from_uint(count_overprogram),
    };
    return mp_obj_new_tuple(4, items);
}
static MP_DEFINE_CONST_FUN_OBJ_0(fake_counters_obj, fake_counters);

static mp_obj_t fake_clear_counters(void) {
    memset(count_ops, 0, sizeof(count_ops));
    count_overprogram = 0;
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_0(fake_clear_counters_obj, fake_clear_counters);

// inject(op, n): the n-th next operation of kind op (0 read, 1 write, 2 erase) fails with EIO.
// inject(None) cancels.
static mp_obj_t fake_inject(size_t n_args, const mp_obj_t *args) {
    if (args[0] == mp_const_none) {
        inject_op = -1;
        return mp_const_none;
    }
    mp_int_t op = mp_obj_get_int(args[0]);
    if (op < OP_READ || op > OP_ERASE) {
        mp_raise_ValueError(MP_ERROR_TEXT("op must be 0, 1 or 2"));
    }
    mp_int_t n = n_args > 1 ? mp_obj_get_int(args[1]) : 1;
    if (n < 1) {
        mp_raise_ValueError(MP_ERROR_TEXT("n must be at least 1"));
    }
    inject_op = op;
    inject_after = n;
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(fake_inject_obj, 1, 2, fake_inject);

// request_ram() -> bytes of the request region
static mp_obj_t fake_request_ram(void) {
    return mp_obj_new_bytes((const byte *)request_ram, sizeof(request_ram));
}
static MP_DEFINE_CONST_FUN_OBJ_0(fake_request_ram_obj, fake_request_ram);

static mp_obj_t fake_set_request_ram(mp_obj_t data_in) {
    mp_buffer_info_t bufinfo;
    mp_get_buffer_raise(data_in, &bufinfo, MP_BUFFER_READ);
    if (bufinfo.len > sizeof(request_ram)) {
        mp_raise_ValueError(MP_ERROR_TEXT("too long"));
    }
    memset(request_ram, 0, sizeof(request_ram));
    memcpy(request_ram, bufinfo.buf, bufinfo.len);
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_1(fake_set_request_ram_obj, fake_set_request_ram);

// retention() -> int; retention(value) sets it
static mp_obj_t fake_retention(size_t n_args, const mp_obj_t *args) {
    if (n_args > 0) {
        retention_word = mp_obj_get_uint(args[0]);
    }
    return mp_obj_new_int_from_uint(retention_word);
}
static MP_DEFINE_CONST_FUN_OBJ_VAR_BETWEEN(fake_retention_obj, 0, 1, fake_retention);

static mp_obj_t fake_resets(void) {
    return mp_obj_new_int_from_uint(reset_count);
}
static MP_DEFINE_CONST_FUN_OBJ_0(fake_resets_obj, fake_resets);

static void check_bootutil(int rc) {
    if (rc != 0) {
        mp_raise_OSError(MP_EIO);
    }
}

// boot_initial(): trailer of the primary slot as left by an initial image programmed with
// "--pad --confirm": magic and image_ok set.
static mp_obj_t fake_boot_initial(void) {
    const struct flash_area *pri;
    check_bootutil(flash_area_open(FLASH_AREA_IMAGE_PRIMARY(0), &pri));
    int rc = boot_write_magic(pri);
    if (rc == 0) {
        rc = boot_write_image_ok(pri);
    }
    flash_area_close(pri);
    check_bootutil(rc);
    return mp_const_none;
}
static MP_DEFINE_CONST_FUN_OBJ_0(fake_boot_initial_obj, fake_boot_initial);

// boot_swap(): the state after the bootloader completed a test swap. The image bytes of the
// primary slot and of the update base of the secondary slot are exchanged, both trailers are
// cleared and the primary trailer gets magic and copy_done (image_ok stays unset).
static mp_obj_t fake_boot_swap(void) {
    #if !defined(MCUBOOT_SWAP_USING_OFFSET)
    mp_raise_NotImplementedError(MP_ERROR_TEXT("swap mode"));
    #else
    const struct flash_area *pri;
    const struct flash_area *sec;
    check_bootutil(flash_area_open(FLASH_AREA_IMAGE_PRIMARY(0), &pri));
    check_bootutil(flash_area_open(FLASH_AREA_IMAGE_SECONDARY(0), &sec));
    uint32_t erase = mcuboot_area_erase(pri);
    uint32_t spare = erase;
    uint32_t image_len = MCUBOOT_MAX_IMAGE_SIZE / erase * erase;
    uint32_t pri_size = flash_area_get_size(pri);
    uint32_t sec_size = flash_area_get_size(sec);

    uint8_t *a = m_new(uint8_t, erase);
    uint8_t *b = m_new(uint8_t, erase);
    int rc = 0;
    for (uint32_t off = 0; rc == 0 && off < image_len; off += erase) {
        rc = flash_area_read(pri, off, a, erase);
        if (rc == 0) {
            rc = flash_area_read(sec, spare + off, b, erase);
        }
        if (rc == 0) {
            rc = flash_area_erase(pri, off, erase);
        }
        if (rc == 0) {
            rc = flash_area_erase(sec, spare + off, erase);
        }
        if (rc == 0) {
            rc = flash_area_write(pri, off, b, erase);
        }
        if (rc == 0) {
            rc = flash_area_write(sec, spare + off, a, erase);
        }
    }
    m_del(uint8_t, a, erase);
    m_del(uint8_t, b, erase);
    if (rc == 0) {
        rc = flash_area_erase(pri, image_len, pri_size - image_len);
    }
    if (rc == 0) {
        rc = flash_area_erase(sec, sec_size - (pri_size - image_len), pri_size - image_len);
    }
    if (rc == 0) {
        rc = boot_write_magic(pri);
    }
    if (rc == 0) {
        rc = boot_write_copy_done(pri);
    }
    flash_area_close(pri);
    flash_area_close(sec);
    check_bootutil(rc);
    return mp_const_none;
    #endif
}
static MP_DEFINE_CONST_FUN_OBJ_0(fake_boot_swap_obj, fake_boot_swap);

// layout_id() -> MCUBOOT_LAYOUT_ID of this build
static mp_obj_t fake_layout_id(void) {
    return mp_obj_new_int_from_uint(MCUBOOT_LAYOUT_ID);
}
static MP_DEFINE_CONST_FUN_OBJ_0(fake_layout_id_obj, fake_layout_id);

// enter_bootloader(*args): the function a port uses for machine.bootloader()
static mp_obj_t fake_enter_bootloader(size_t n_args, const mp_obj_t *args) {
    mcuboot_app_enter_bootloader(n_args, args);
}
static MP_DEFINE_CONST_FUN_OBJ_VAR(fake_enter_bootloader_obj, 0, fake_enter_bootloader);

static const mp_rom_map_elem_t fake_module_globals_table[] = {
    { MP_ROM_QSTR(MP_QSTR___name__), MP_ROM_QSTR(MP_QSTR_mcuboot_fake) },
    { MP_ROM_QSTR(MP_QSTR_geometry), MP_ROM_PTR(&fake_geometry_obj) },
    { MP_ROM_QSTR(MP_QSTR_erase_all), MP_ROM_PTR(&fake_erase_all_obj) },
    { MP_ROM_QSTR(MP_QSTR_read), MP_ROM_PTR(&fake_read_obj) },
    { MP_ROM_QSTR(MP_QSTR_write), MP_ROM_PTR(&fake_write_obj) },
    { MP_ROM_QSTR(MP_QSTR_erase), MP_ROM_PTR(&fake_erase_obj) },
    { MP_ROM_QSTR(MP_QSTR_counters), MP_ROM_PTR(&fake_counters_obj) },
    { MP_ROM_QSTR(MP_QSTR_clear_counters), MP_ROM_PTR(&fake_clear_counters_obj) },
    { MP_ROM_QSTR(MP_QSTR_inject), MP_ROM_PTR(&fake_inject_obj) },
    { MP_ROM_QSTR(MP_QSTR_request_ram), MP_ROM_PTR(&fake_request_ram_obj) },
    { MP_ROM_QSTR(MP_QSTR_set_request_ram), MP_ROM_PTR(&fake_set_request_ram_obj) },
    { MP_ROM_QSTR(MP_QSTR_retention), MP_ROM_PTR(&fake_retention_obj) },
    { MP_ROM_QSTR(MP_QSTR_resets), MP_ROM_PTR(&fake_resets_obj) },
    { MP_ROM_QSTR(MP_QSTR_layout_id), MP_ROM_PTR(&fake_layout_id_obj) },
    { MP_ROM_QSTR(MP_QSTR_enter_bootloader), MP_ROM_PTR(&fake_enter_bootloader_obj) },
    { MP_ROM_QSTR(MP_QSTR_boot_initial), MP_ROM_PTR(&fake_boot_initial_obj) },
    { MP_ROM_QSTR(MP_QSTR_boot_swap), MP_ROM_PTR(&fake_boot_swap_obj) },
};
static MP_DEFINE_CONST_DICT(fake_module_globals, fake_module_globals_table);

const mp_obj_module_t mp_module_mcuboot_fake = {
    .base = { &mp_type_module },
    .globals = (mp_obj_dict_t *)&fake_module_globals,
};

MP_REGISTER_MODULE(MP_QSTR_mcuboot_fake, mp_module_mcuboot_fake);
