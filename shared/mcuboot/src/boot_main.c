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

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <string.h>

#include "bootutil/bootutil.h"
#include "bootutil/bootutil_public.h"
#include "bootutil/fault_injection_hardening.h"
#include "bootutil/image.h"
#include "flash_map_backend/flash_map_backend.h"
#include "mcuboot_config/mcuboot_config.h"
#include "mcuboot_config/mcuboot_logging.h"
#include "mcuboot_dfu.h"
#include "mcuboot_fsload.h"
#include "mcuboot_log.h"
#include "mcuboot_port.h"
#include "mcuboot_request.h"
#include "mcuboot_types.h"
#include "mcuboot_validate.h"
#include "sysflash/sysflash.h"

#if defined(MCUBOOT_HW_ROLLBACK_PROT)
#include "bootutil/security_cnt.h"
#endif

// Boot decision of the bootloader: reads the hardware and request inputs, runs boot_go(),
// jumps into the primary slot, and handles recovery (fsload retry, DFU) when it can't jump.

// Bits of mcuboot_port_led().
#define LED_ALIVE (1u << 0)
#define LED_ERROR (1u << 2)

// Consecutive resets from the fault path (faults, NMI, assert paths of the port) after which the
// bootloader stops running boot_go() and goes to recovery with cause REC_FAULT.
#define FAULT_RESETS_MAX 3u

// Ports that count the resets caused by their fault handlers override this.
__attribute__((weak)) uint32_t mcuboot_port_fault_resets(void) {
    return 0;
}

static bool s_in_recovery_front_end;
static bool s_recovering;
// The security counter area is damaged. Every image fails the rollback check, which says
// nothing about the images themselves.
static bool s_seccnt_failed;

// Runs the DFU front end, which does not return. Without one there is nothing to run: the
// LED shows the error and the loop waits for a reset.
static MCUBOOT_NORETURN void enter_recovery(mcuboot_recovery_cause_t why) {
    #if MCUBOOT_DFU_ENABLE
    s_in_recovery_front_end = true;
    mcuboot_dfu_run(why);
    #else
    MCUBOOT_LOG_ERR("no front end for recovery cause %d", (int)why);
    mcuboot_port_led(LED_ALIVE | LED_ERROR);
    for (;;) {
        mcuboot_port_wdt_feed();
        mcuboot_port_delay_ms(100);
    }
    #endif
}

// ---- primary slot checks ----

static const struct flash_area *primary_area(void) {
    const struct flash_area *fap = NULL;
    if (flash_area_open(FLASH_AREA_IMAGE_PRIMARY(0), &fap) != 0) {
        return NULL;
    }
    return fap;
}

#if MCUBOOT_FSLOAD_ENABLE
// True if the primary slot holds an image that passes hash and signature.
static bool primary_bootable(void) {
    const struct flash_area *pri = primary_area();
    return pri != NULL && mcuboot_validate_view(pri, VALIDATE_FULL).code == MCUBOOT_RES_OK;
}
#endif

#if !MCUBOOT_POLICY_SINGLE
// Recovery step before the front end starts after boot_go() failed or asserted. A primary slot
// with no valid image can still carry swap state and a header that bootutil takes for an image
// (an interrupted swap whose size can no longer be read asserts on every boot). Its trailer and
// first sector are erased so an image installed afterwards will boot. A primary slot with a
// valid image is left alone.
static void recover_primary(void) {
    const struct flash_area *pri = primary_area();
    if (pri == NULL) {
        return;
    }
    if (s_seccnt_failed) {
        // Every image fails the rollback check while the counter cannot be read: the primary
        // slot is kept so that repairing the counter area brings the application back.
        MCUBOOT_LOG_WRN("security counter area damaged, the primary slot is left as it is");
        return;
    }
    mcuboot_validate_result_t r = mcuboot_validate_view(pri, VALIDATE_FULL);
    if (r.code == MCUBOOT_RES_OK) {
        return;
    }
    MCUBOOT_LOG_WRN("primary slot has no valid image (code %d), erasing its trailer and header", (int)r.code);
    int rc = mcuboot_flash_erase_trailer(FLASH_AREA_IMAGE_PRIMARY(0));
    if (rc == 0) {
        rc = flash_area_erase(pri, 0, mcuboot_area_erase(pri));
    }
    if (rc != 0) {
        MCUBOOT_LOG_ERR("primary slot erase failed rc=%d", rc);
    }
}
#else
static void recover_primary(void) {
}
#endif

MCUBOOT_NORETURN void mcuboot_fault_recover(void) {
    if (s_in_recovery_front_end) {
        // A failure inside the front end (a callback asserting): restart and let the main flow
        // decide again.
        mcuboot_port_deinit();
        mcuboot_port_reset();
    }
    // A failure while recovering goes straight to the front end.
    if (!s_recovering) {
        s_recovering = true;
        recover_primary();
    }
    enter_recovery(REC_FAULT);
}


#if !MCUBOOT_POLICY_SINGLE
// A pending update that this layout cannot take is not swapped in: one built for another flash
// layout (layout id TLV wrong or missing), or with swap using offset one of at most one erase
// unit, which an interrupted swap would not resume. Images can reach the secondary slot without
// the DFU or fsload checks (the application writer, a programmer), so they are judged here before
// boot_go() installs them. Only the layout and the size are checked; hash, signature and security
// counter stay with boot_go(). The rejection erases the pending state and the image headers of
// the secondary slot, trailer first, so an interrupted rejection leaves either the update still
// pending (rejected again) or a slot with no pending update.
static bool update_layout_rejected(const struct flash_area *sec) {
    mcuboot_validate_result_t r = mcuboot_validate_view(sec, VALIDATE_CHECK_TARGET | VALIDATE_STRUCTURE_ONLY);
    if (r.code == MCUBOOT_RES_ERR_LAYOUT) {
        MCUBOOT_LOG_ERR("update image has layout id %08x, this bootloader has %08x: rejected", (unsigned)r.detail, (unsigned)MCUBOOT_LAYOUT_ID);
    } else if (r.code == MCUBOOT_RES_ERR_TOO_SMALL) {
        MCUBOOT_LOG_ERR("update image of %u bytes is not larger than one erase unit: rejected", (unsigned)r.detail);
    } else {
        return false;
    }
    uint32_t unit = mcuboot_area_erase(sec);
    int rc = mcuboot_flash_erase_trailer(FLASH_AREA_IMAGE_SECONDARY(0));
    if (rc == 0) {
        rc = flash_area_erase(sec, 0, 2 * unit);
    }
    if (rc != 0) {
        MCUBOOT_LOG_ERR("erase of the rejected update failed rc=%d", rc);
        enter_recovery(REC_FAULT);
    }
    return true;
}
#endif

#if defined(MCUBOOT_SWAP_USING_OFFSET)
// A swap using offset leaves the replaced image at the start of the secondary slot, while an
// update lives one erase unit further in. With no image in the primary slot, bootutil's
// bootstrap accepts a header at either place as the image to install but always copies from one
// erase unit in. A stale image at the start makes it erase the whole primary slot and copy the
// wrong bytes on every boot. Nothing can be installed from there, so the stale header is erased.
// An interrupted swap also has no image header in the primary slot while its first sector is
// being copied, and the image that was there is then at the start of the secondary slot. The
// primary trailer state (swap started, copy not done) tells the two cases apart.
static void drop_stale_secondary_header(void) {
    const struct flash_area *sec;
    struct image_header hdr;
    struct boot_swap_state state;
    if (boot_read_swap_state_by_id(FLASH_AREA_IMAGE_PRIMARY(0), &state) != 0
        || (state.magic == BOOT_MAGIC_GOOD && state.copy_done == BOOT_FLAG_UNSET && state.swap_type != BOOT_SWAP_TYPE_NONE)) {
        return;
    }
    if (flash_area_open(FLASH_AREA_IMAGE_SECONDARY(0), &sec) != 0 || flash_area_read(sec, 0, &hdr, sizeof(hdr)) != 0
        || hdr.ih_magic != IMAGE_MAGIC) {
        return;
    }
    MCUBOOT_LOG_WRN("dropping the stale image header at the start of the secondary slot");
    int rc = flash_area_erase(sec, 0, mcuboot_area_erase(sec));
    if (rc != 0) {
        MCUBOOT_LOG_ERR("erase of the stale header failed rc=%d", rc);
        enter_recovery(REC_FAULT);
    }
}
#endif

#if MCUBOOT_POLICY_OVERWRITE_EXTERNAL
// A copy cut after the secondary slot header was erased and before its pending state was
// cleared leaves a pending update on a slot with no image. bootutil finds nothing to install on
// every boot, so the pending state is dropped.
static void drop_pending_without_image(void) {
    const struct flash_area *sec;
    struct image_header hdr;
    struct boot_swap_state state;
    if (flash_area_open(FLASH_AREA_IMAGE_SECONDARY(0), &sec) != 0 || flash_area_read(sec, 0, &hdr, sizeof(hdr)) != 0
        || hdr.ih_magic == IMAGE_MAGIC || boot_read_swap_state_by_id(FLASH_AREA_IMAGE_SECONDARY(0), &state) != 0
        || state.magic == BOOT_MAGIC_UNSET) {
        return;
    }
    MCUBOOT_LOG_WRN("dropping the pending state of an empty secondary slot");
    int rc = mcuboot_flash_erase_trailer(FLASH_AREA_IMAGE_SECONDARY(0));
    if (rc != 0) {
        MCUBOOT_LOG_ERR("erase of the secondary trailer failed rc=%d", rc);
        enter_recovery(REC_FAULT);
    }
}
#endif

// ---- main flow ----

int mcuboot_main(void) {
    static mcuboot_request_t req;

    mcuboot_port_early_init();
    MCUBOOT_LOG_INF("MPY MCUboot %s layout %08x", MCUBOOT_BL_VERSION, (unsigned)MCUBOOT_LAYOUT_ID);

    // First consumer of the handoff: afterwards the request memory and the retention word are
    // zero, so a crash or reset during recovery lands in normal boot.
    mcuboot_request_take(&req, mcuboot_port_reset_cause());

    // Flash checks. Recovery must stay reachable when they fail, so the tables are not
    // trusted and there is no recovery step on the slots here.
    int rc = mcuboot_port_flash_init();
    if (rc == 0) {
        rc = mcuboot_flash_map_check();
    }
    if (rc != 0) {
        MCUBOOT_LOG_ERR("flash map check failed rc=%d", rc);
        enter_recovery(REC_FAULT);
    }

    if (mcuboot_port_fault_resets() >= FAULT_RESETS_MAX) {
        MCUBOOT_LOG_ERR("repeated fault resets, entering recovery");
        enter_recovery(REC_FAULT);
    }

    rc = mcuboot_flash_scrub();
    if (rc != 0) {
        MCUBOOT_LOG_ERR("flash scrub failed rc=%d", rc);
        enter_recovery(REC_FAULT);
    }
    #if defined(MCUBOOT_HW_ROLLBACK_PROT)
    fih_ret cnt_rc = boot_nv_security_counter_init();
    if (FIH_NOT_EQ(cnt_rc, FIH_SUCCESS)) {
        MCUBOOT_LOG_ERR("security counter init failed, the counter area is damaged or unreadable");
        s_seccnt_failed = true;
    }
    #endif

    bool forced = mcuboot_port_entry_forced();
    bool recovery = req.mode != MCUBOOT_REQ_NONE || forced;
    mcuboot_recovery_cause_t why = req.mode != MCUBOOT_REQ_NONE ? REC_APP_REQUEST : forced ? REC_FORCED : REC_NONE;

    // boot_go() runs unless recovery was asked for, with one exception: a pending test swap,
    // permanent swap or revert is completed first (settle). A DFU session erases the secondary
    // slot, which holds the revert image while a test image is unconfirmed, and in offset mode
    // the revert state lives in its trailer.
    int swap_before = BOOT_SWAP_TYPE_NONE;
    #if MCUBOOT_POLICY_OVERWRITE_EXTERNAL
    drop_pending_without_image();
    #endif
    #if !MCUBOOT_POLICY_SINGLE
    swap_before = boot_swap_type_multi(0);
    #endif
    bool settle = recovery && (swap_before == BOOT_SWAP_TYPE_TEST || swap_before == BOOT_SWAP_TYPE_PERM || swap_before == BOOT_SWAP_TYPE_REVERT);

    if (!recovery || settle) {
        mcuboot_image_info_t pre;
        const struct flash_area *pri = primary_area();
        memset(&pre, 0, sizeof(pre));
        if (pri != NULL) {
            mcuboot_image_info_read(pri, &pre);
        }
        #if defined(MCUBOOT_SWAP_USING_OFFSET)
        if (!pre.valid && swap_before == BOOT_SWAP_TYPE_NONE) {
            drop_stale_secondary_header();
        }
        #endif
        #if !MCUBOOT_POLICY_SINGLE
        if (swap_before == BOOT_SWAP_TYPE_TEST || swap_before == BOOT_SWAP_TYPE_PERM) {
            const struct flash_area *sec;
            if (flash_area_open(FLASH_AREA_IMAGE_SECONDARY(0), &sec) == 0) {
                if (update_layout_rejected(sec)) {
                    swap_before = BOOT_SWAP_TYPE_NONE;
                }
            }
        }
        #endif

        struct boot_rsp rsp;
        FIH_DECLARE(fih_rc, FIH_FAILURE);
        FIH_CALL(boot_go, fih_rc, &rsp);
        if (FIH_EQ(fih_rc, FIH_SUCCESS)) {
            #if MCUBOOT_POLICY_SINGLE && defined(MCUBOOT_HW_ROLLBACK_PROT)
            if (mcuboot_validate_raise_counter() != 0) {
                MCUBOOT_LOG_ERR("security counter update failed");
                enter_recovery(REC_FAULT);
            }
            #endif
            if (!recovery) {
                mcuboot_port_deinit();
                mcuboot_port_jump(mcuboot_dev_base(rsp.br_flash_dev_id) + rsp.br_image_off + rsp.br_hdr->ih_hdr_size);
            }
            // Recovery was requested and the pending swap or revert is now complete: stay in the bootloader.
        } else {
            MCUBOOT_LOG_ERR("no bootable image");
            recover_primary();
            why = REC_NO_IMAGE;
            #if MCUBOOT_POLICY_SINGLE && MCUBOOT_FSLOAD_ENABLE
            // Retry after a power loss during a single-slot fsload. The element stream saved
            // in the intent area before the slot was overwritten requests the load again.
            if (mcuboot_intent_load(&req)) {
                req.mode = MCUBOOT_REQ_FSLOAD;
            }
            #endif
        }
    }

    #if MCUBOOT_FSLOAD_ENABLE
    if (req.mode == MCUBOOT_REQ_FSLOAD) {
        int r = mcuboot_fsload_run(req.elems, req.elems_len);
        // After a good fsload the new image is pending (swap) or in place (single): reset so
        // boot_go() starts clean. After a failed one the old image boots if there is one.
        if (r == MCUBOOT_RES_OK || primary_bootable()) {
            mcuboot_port_deinit();
            mcuboot_port_reset();
        }
        why = REC_FSLOAD_FAILED;
    }
    #endif

    enter_recovery(why);
}
