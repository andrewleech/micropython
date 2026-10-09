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

#define _GNU_SOURCE
#include <stdio.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>

#include "bootutil/bootutil_public.h"
#include "flash_map_backend/flash_map_backend.h"
#include "mcuboot_config/mcuboot_config.h"
#include "mboot_log.h"
#include "mboot_port.h"
#include "mboot_validate.h"
#include "sysflash/sysflash.h"
#include "bl.h"

// The port functions of the bootloader for the host. The flash functions are in
// tests/mboot/host/fake_flash.c, the USB side in bl_host.c.

bl_shared_t *bl_shared;

// The linker symbols of ports/stm32/mboot/mcuboot/mboot_dev.h. The request region is
// mboot_port_request_ram() below; fsload only compares the address of an application status
// word with the status window, which is a RAM array here that ends at mboot_status_end.
char mboot_req_start[1];
char mboot_status_start[256];
__asm__ (".globl mboot_status_end\n.set mboot_status_end, mboot_status_start + 256");

void bl_port_init(void) {
    if (bl_shared == NULL) {
        bl_shared = mmap(NULL, sizeof(*bl_shared), PROT_READ | PROT_WRITE, MAP_SHARED | MAP_ANONYMOUS, -1, 0);
        if (bl_shared == MAP_FAILED) {
            perror("mmap");
            _exit(2);
        }
        memset(bl_shared, 0, sizeof(*bl_shared));
    }
}

void mboot_port_early_init(void) {
}

void mboot_port_deinit(void) {
}

// The application would start here: record what runs.
void bl_record_jump(uint32_t vt_addr) {
    const struct flash_area *pri = NULL;
    bl_shared->out.kind = BL_OUT_JUMP;
    bl_shared->out.jump_addr = vt_addr;
    #if MBOOT_POLICY_SINGLE
    bl_shared->out.swap_type = BOOT_SWAP_TYPE_NONE;    // the only slot has no swap state
    #else
    bl_shared->out.swap_type = boot_swap_type_multi(0);
    #endif
    if (flash_area_open(FLASH_AREA_IMAGE_PRIMARY(0), &pri) == 0) {
        mboot_image_info_read(pri, &bl_shared->out.running);
    }
}

MBOOT_NORETURN void mboot_port_jump(uint32_t vt_addr) {
    bl_record_jump(vt_addr);
    _exit(BL_EXIT_JUMP);
}

MBOOT_NORETURN void mboot_port_reset(void) {
    if (bl_shared->out.kind == BL_OUT_NONE) {
        bl_shared->out.kind = BL_OUT_RESET;
    }
    _exit(BL_EXIT_RESET);
}

uint32_t mboot_port_reset_cause(void) {
    return bl_shared->reset_cause;
}

static uint32_t s_ticks;

void mboot_port_delay_ms(uint32_t ms) {
    s_ticks += ms;
}

uint32_t mboot_port_ticks_ms(void) {
    return s_ticks;
}

void mboot_port_wdt_feed(void) {
    bl_shared->wdt_feeds++;
}

bool mboot_port_entry_forced(void) {
    return bl_shared->entry_forced;
}

uint32_t mboot_port_fault_resets(void) {
    return bl_shared->fault_resets;
}

uint32_t mboot_port_retention_read(void) {
    return bl_shared->retention;
}

void mboot_port_retention_write(uint32_t v) {
    bl_shared->retention = v;
}

void *mboot_port_request_ram(size_t *size_out) {
    if (size_out != NULL) {
        *size_out = sizeof(bl_shared->req_ram);
    }
    return bl_shared->req_ram;
}

int mboot_port_usb_init(void) {
    return 0;
}

void mboot_port_usb_deinit(void) {
}

void mboot_port_usb_serial_number(char *buf, size_t len) {
    snprintf(buf, len, "BLHOST");
}

void mboot_port_led(uint32_t mask) {
    bl_shared->led = mask;
}

void mboot_port_log_write(const char *s, size_t n) {
    bl_shared->log_lines++;
    if (n >= sizeof(bl_shared->log_tail)) {
        s += n - (sizeof(bl_shared->log_tail) - 1);
        n = sizeof(bl_shared->log_tail) - 1;
    }
    if (bl_shared->log_tail_len + n >= sizeof(bl_shared->log_tail)) {
        size_t keep = sizeof(bl_shared->log_tail) / 2;
        memmove(bl_shared->log_tail, bl_shared->log_tail + bl_shared->log_tail_len - keep, keep);
        bl_shared->log_tail_len = keep;
    }
    memcpy(bl_shared->log_tail + bl_shared->log_tail_len, s, n);
    bl_shared->log_tail_len += n;
    if (bl_shared->log_echo) {
        fwrite(s, 1, n, stdout);
        fflush(stdout);
    }
}

// tinycrypt's ecc.c names default_CSPRNG in a data initialiser. Signature verification never
// calls it.
int default_CSPRNG(uint8_t *dest, unsigned int size) {
    (void)dest;
    (void)size;
    return 0;
}
