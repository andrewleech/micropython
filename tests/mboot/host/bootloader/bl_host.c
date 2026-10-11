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

#include <string.h>
#include <unistd.h>

#include "flash_map_backend/flash_map_backend.h"
#include "mboot_api.h"
#include "mboot_usbd.h"
#include "mboot_dfu_recovery.h"
#include "mboot_port.h"
#include "mboot_validate.h"
#include "sysflash/sysflash.h"
#include "tusb.h"
#include "bl.h"

// The USB side of the host harness: the real mboot_dfu_recovery_run() from dfu_glue.c runs its loop
// over a fake TinyUSB, and tud_task() plays the DFU host. The host does what dfu-util or
// pydfu do: it sends the image in 2048 byte blocks to the DFU core's download callback, ends the
// download with the manifest step, reads the result of vendor request 0x81 and then resets the
// bus, which makes the loop leave through mboot_port_reset().

MBOOT_NORETURN void __real_mboot_dfu_recovery_run(mboot_recovery_cause_t why);

// Records that the DFU front end started, with its cause and the image in the primary slot at
// that moment (after a pending swap or revert has been settled).
MBOOT_NORETURN void __wrap_mboot_dfu_recovery_run(mboot_recovery_cause_t why) {
    const struct flash_area *pri = NULL;
    bl_dfu_out_t *o = &bl_shared->out.dfu;
    o->entered = true;
    o->why = (int)why;
    o->manifest_status = 0xFF;
    if (flash_area_open(FLASH_AREA_IMAGE_PRIMARY(0), &pri) == 0) {
        mboot_image_info_read(pri, &o->primary);
    }
    __real_mboot_dfu_recovery_run(why);
}

bool tusb_init(void) {
    return true;
}

static MBOOT_NORETURN void end_waiting(void) {
    bl_shared->out.kind = BL_OUT_DFU_WAIT;
    _exit(BL_EXIT_DFU);
}

static uint32_t get_le32(const uint8_t *p) {
    return p[0] | (uint32_t)p[1] << 8 | (uint32_t)p[2] << 16 | (uint32_t)p[3] << 24;
}

void bl_dfu_session(void) {
    bl_script_t *sc = &bl_shared->script;
    bl_dfu_out_t *o = &bl_shared->out.dfu;
    uint8_t data[2048];

    if (sc->kind == BL_SCRIPT_NONE) {
        end_waiting();
    }
    o->ran = true;
    size_t pos = 0;
    unsigned n = 0;
    while (pos < sc->len) {
        if (sc->max_blocks != 0 && n >= sc->max_blocks) {
            end_waiting();
        }
        size_t l = sc->len - pos < sizeof(data) ? sc->len - pos : sizeof(data);
        memcpy(data, sc->blob + pos, l);
        fake_tusb_reset();
        tud_dfu_download_cb(0, (uint16_t)n, data, (uint16_t)l);
        o->block_status = fake_tusb_last_finish_status;
        o->blocks_sent = ++n;
        if (o->block_status != DFU_STATUS_OK) {
            end_waiting();
        }
        pos += l;
    }
    if (sc->omit_manifest) {
        end_waiting();
    }

    fake_tusb_reset();
    tud_dfu_manifest_cb(0);
    o->manifest_status = fake_tusb_last_finish_status;
    uint8_t r[MBOOT_DFU_RESULT_WIRE_LEN];
    if (mboot_hook_get_result(r, sizeof(r)) == sizeof(r)) {
        o->result.seq = get_le32(r);
        o->result.code = (uint16_t)(r[4] | r[5] << 8);
        o->result.source = r[6];
        o->result.phase = r[7];
        o->result.detail = get_le32(r + 8);
    }
    if (o->manifest_status != DFU_STATUS_OK) {
        end_waiting();
    }
    // The bus reset after a completed download: the session loop leaves.
    mboot_usbd_bus_reset();
}

void tud_task(void) {
    static bool done;
    if (!done) {
        done = true;
        bl_dfu_session();
    }
}
