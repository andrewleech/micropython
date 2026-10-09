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
#include "mboot_dfu_recovery.h"
#include "mboot_fsload.h"
#include "mboot_log.h"
#include "mboot_port.h"
#include "mboot_request.h"
#include "mboot_types.h"
#include "mboot_updatelog.h"
#include "mboot_validate.h"
#include "sysflash/sysflash.h"

#if defined(MCUBOOT_HW_ROLLBACK_PROT)
#include "bootutil/security_cnt.h"
#endif

// Boot decision of the bootloader: reads the hardware and request inputs, runs boot_go(),
// jumps into the primary slot, and handles recovery (fsload retry, DFU) when it can't jump.

// Bits of mboot_port_led().
#define LED_ALIVE (1u << 0)
#define LED_ERROR (1u << 2)

// Consecutive resets from the fault path (faults, NMI, assert paths of the port) after which the
// bootloader stops running boot_go() and goes to recovery with cause REC_FAULT.
#define FAULT_RESETS_MAX 3u

// Ports that count the resets caused by their fault handlers override this.
__attribute__((weak)) uint32_t mboot_port_fault_resets(void) {
    return 0;
}

static bool s_in_recovery_front_end;
static bool s_recovering;
// The security counter area is damaged. Every image fails the rollback check, which says
// nothing about the images themselves.
static bool s_seccnt_failed;

// Runs the DFU front end, which does not return. Without one there is nothing to run: the
// LED shows the error and the loop waits for a reset.
static MBOOT_NORETURN void enter_recovery(mboot_recovery_cause_t why) {
    #if MBOOT_DFU_ENABLE
    s_in_recovery_front_end = true;
    mboot_dfu_recovery_run(why);
    #else
    MCUBOOT_LOG_ERR("no front end for recovery cause %d", (int)why);
    mboot_port_led(LED_ALIVE | LED_ERROR);
    for (;;) {
        mboot_port_wdt_feed();
        mboot_port_delay_ms(100);
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

#if MBOOT_FSLOAD_ENABLE
// True if the primary slot holds an image that passes hash and signature.
static bool primary_bootable(void) {
    const struct flash_area *pri = primary_area();
    return pri != NULL && mboot_validate_view(pri, VALIDATE_FULL).code == MBOOT_RES_OK;
}
#endif

#if !MBOOT_POLICY_SINGLE
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
    mboot_validate_result_t r = mboot_validate_view(pri, VALIDATE_FULL);
    if (r.code == MBOOT_RES_OK) {
        return;
    }
    MCUBOOT_LOG_WRN("primary slot has no valid image (code %d), erasing its trailer and header", (int)r.code);
    int rc = mboot_flash_erase_trailer(FLASH_AREA_IMAGE_PRIMARY(0));
    if (rc == 0) {
        rc = flash_area_erase(pri, 0, mboot_area_erase(pri));
    }
    if (rc != 0) {
        MCUBOOT_LOG_ERR("primary slot erase failed rc=%d", rc);
    }
}
#else
static void recover_primary(void) {
}
#endif

MBOOT_NORETURN void mboot_fault_recover(void) {
    if (s_in_recovery_front_end) {
        // A failure inside the front end (a callback asserting): restart and let the main flow
        // decide again.
        mboot_port_deinit();
        mboot_port_reset();
    }
    // A failure while recovering goes straight to the front end.
    if (!s_recovering) {
        s_recovering = true;
        recover_primary();
    }
    enter_recovery(REC_FAULT);
}

// ---- update audit log after boot_go() ----

static bool info_equal(const mboot_image_info_t *a, const mboot_image_info_t *b) {
    return a->valid == b->valid && a->ver_major == b->ver_major && a->ver_minor == b->ver_minor && a->ver_rev == b->ver_rev
           && a->ver_build == b->ver_build && a->sec_cnt == b->sec_cnt && a->hash_prefix == b->hash_prefix;
}

static bool info_older(const mboot_image_info_t *a, const mboot_image_info_t *b) {
    if (a->ver_major != b->ver_major) {
        return a->ver_major < b->ver_major;
    }
    if (a->ver_minor != b->ver_minor) {
        return a->ver_minor < b->ver_minor;
    }
    return a->ver_rev < b->ver_rev;
}

// Records what boot_go() did, from the swap type before it ran and the primary slot before and
// after. cand is the header of the update image that was in the secondary slot before.
static void log_after_boot_go(int swap_before, const mboot_image_info_t *pre, const mboot_image_info_t *cand) {
    mboot_image_info_t post;
    const struct flash_area *pri = primary_area();
    if (pri == NULL) {
        return;
    }
    mboot_image_info_read(pri, &post);
    bool changed = !info_equal(pre, &post);
    uint32_t detail = (uint32_t)swap_before;

    if (swap_before == BOOT_SWAP_TYPE_TEST || swap_before == BOOT_SWAP_TYPE_PERM) {
        if (!changed) {
            MCUBOOT_LOG_WRN("update image rejected by boot_go()");
            mboot_updatelog_append(LOG_SLOT_REJECTED_AT_BOOT, MBOOT_RES_OK, SRC_BOOT, cand, detail);
            return;
        }
        mboot_updatelog_append(swap_before == BOOT_SWAP_TYPE_TEST ? LOG_SWAP_DONE : LOG_SWAP_DONE_PERM, MBOOT_RES_OK, SRC_BOOT, &post, detail);
    } else if (swap_before == BOOT_SWAP_TYPE_REVERT) {
        if (changed) {
            mboot_updatelog_append(LOG_REVERTED, MBOOT_RES_OK, SRC_BOOT, &post, detail);
        }
    } else if (changed) {
        // An interrupted swap completed, or an image was installed over an empty primary header.
        int after = boot_swap_type_multi(0);
        uint8_t type = after == BOOT_SWAP_TYPE_REVERT ? LOG_SWAP_DONE : (pre->valid && info_older(&post, pre)) ? LOG_REVERTED : LOG_SWAP_DONE_PERM;
        mboot_updatelog_append(type, MBOOT_RES_OK, SRC_BOOT, &post, detail);
    }
}

#if !MBOOT_POLICY_SINGLE
// A pending update that this layout cannot take is not swapped in: one built for another flash
// layout (layout id TLV wrong or missing), or with swap using offset one of at most one erase
// unit, which an interrupted swap would not resume. Images can reach the secondary slot without
// the DFU or fsload checks (the application writer, a programmer), so they are judged here before
// boot_go() installs them. Only the layout and the size are checked; hash, signature and security
// counter stay with boot_go(). The rejection erases the pending state and the image headers of
// the secondary slot, trailer first, so an interrupted rejection leaves either the update still
// pending (rejected again) or a slot with no pending update.
static bool update_layout_rejected(const struct flash_area *sec, const mboot_image_info_t *cand, int swap_before) {
    mboot_validate_result_t r = mboot_validate_view(sec, VALIDATE_CHECK_TARGET | VALIDATE_STRUCTURE_ONLY);
    if (r.code == MBOOT_RES_ERR_LAYOUT) {
        MCUBOOT_LOG_ERR("update image has layout id %08x, this bootloader has %08x: rejected", (unsigned)r.detail, (unsigned)MBOOT_LAYOUT_ID);
    } else if (r.code == MBOOT_RES_ERR_TOO_SMALL) {
        MCUBOOT_LOG_ERR("update image of %u bytes is not larger than one erase unit: rejected", (unsigned)r.detail);
    } else {
        return false;
    }
    uint32_t unit = mboot_area_erase(sec);
    int rc = mboot_flash_erase_trailer(FLASH_AREA_IMAGE_SECONDARY(0));
    if (rc == 0) {
        rc = flash_area_erase(sec, 0, 2 * unit);
    }
    if (rc != 0) {
        MCUBOOT_LOG_ERR("erase of the rejected update failed rc=%d", rc);
        enter_recovery(REC_FAULT);
    }
    mboot_updatelog_append(LOG_SLOT_REJECTED_AT_BOOT, (uint8_t)r.code, SRC_BOOT, cand, (uint32_t)swap_before);
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
    int rc = flash_area_erase(sec, 0, mboot_area_erase(sec));
    if (rc != 0) {
        MCUBOOT_LOG_ERR("erase of the stale header failed rc=%d", rc);
        enter_recovery(REC_FAULT);
    }
}
#endif

#if MBOOT_POLICY_OVERWRITE_EXTERNAL
// A copy cut after the secondary slot header was erased and before its pending state was
// cleared leaves a pending update on a slot with no image. bootutil finds nothing to install
// on every boot and the update audit log would get a record each time, so the pending state is
// dropped.
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
    int rc = mboot_flash_erase_trailer(FLASH_AREA_IMAGE_SECONDARY(0));
    if (rc != 0) {
        MCUBOOT_LOG_ERR("erase of the secondary trailer failed rc=%d", rc);
        enter_recovery(REC_FAULT);
    }
}
#endif

// ---- main flow ----

int mboot_main(void) {
    static mboot_request_t req;

    mboot_port_early_init();
    MCUBOOT_LOG_INF("MPY MCUboot %s layout %08x", MBOOT_BL_VERSION, (unsigned)MBOOT_LAYOUT_ID);

    // First consumer of the handoff: afterwards the request memory and the retention word are
    // zero, so a crash or reset during recovery lands in normal boot.
    mboot_request_take(&req, mboot_port_reset_cause());

    // Flash checks. Recovery must stay reachable when they fail, so the tables are not
    // trusted and there is no recovery step on the slots here.
    int rc = mboot_port_flash_dev_init();
    if (rc == 0) {
        rc = mboot_flash_map_check();
    }
    if (rc != 0) {
        MCUBOOT_LOG_ERR("flash map check failed rc=%d", rc);
        enter_recovery(REC_FAULT);
    }

    if (mboot_port_fault_resets() >= FAULT_RESETS_MAX) {
        MCUBOOT_LOG_ERR("repeated fault resets, entering recovery");
        enter_recovery(REC_FAULT);
    }

    rc = mboot_flash_scrub();
    if (rc != 0) {
        MCUBOOT_LOG_ERR("flash scrub failed rc=%d", rc);
        enter_recovery(REC_FAULT);
    }
    #if defined(MCUBOOT_HW_ROLLBACK_PROT)
    fih_ret cnt_rc = boot_nv_security_counter_init();
    if (FIH_NOT_EQ(cnt_rc, FIH_SUCCESS)) {
        MCUBOOT_LOG_ERR("security counter init failed, the counter area is damaged or unreadable");
        s_seccnt_failed = true;
        mboot_updatelog_append(LOG_SECCNT_FAILED, MBOOT_RES_ERR_FLASH, SRC_BOOT, NULL, 0);
    }
    #endif

    bool forced = mboot_port_entry_forced();
    bool recovery = req.mode != MBOOT_REQ_NONE || forced;
    mboot_recovery_cause_t why = req.mode != MBOOT_REQ_NONE ? REC_APP_REQUEST : forced ? REC_FORCED : REC_NONE;

    // boot_go() runs unless recovery was asked for, with one exception: a pending test swap,
    // permanent swap or revert is completed first (settle). A DFU session erases the secondary
    // slot, which holds the revert image while a test image is unconfirmed, and in offset mode
    // the revert state lives in its trailer.
    int swap_before = BOOT_SWAP_TYPE_NONE;
    #if MBOOT_POLICY_OVERWRITE_EXTERNAL
    drop_pending_without_image();
    #endif
    #if !MBOOT_POLICY_SINGLE
    swap_before = boot_swap_type_multi(0);
    #endif
    bool settle = recovery && (swap_before == BOOT_SWAP_TYPE_TEST || swap_before == BOOT_SWAP_TYPE_PERM || swap_before == BOOT_SWAP_TYPE_REVERT);

    if (!recovery || settle) {
        mboot_image_info_t pre;
        mboot_image_info_t cand;
        const struct flash_area *pri = primary_area();
        memset(&pre, 0, sizeof(pre));
        memset(&cand, 0, sizeof(cand));
        if (pri != NULL) {
            mboot_image_info_read(pri, &pre);
        }
        #if defined(MCUBOOT_SWAP_USING_OFFSET)
        if (!pre.valid && swap_before == BOOT_SWAP_TYPE_NONE) {
            drop_stale_secondary_header();
        }
        #endif
        #if !MBOOT_POLICY_SINGLE
        if (swap_before == BOOT_SWAP_TYPE_TEST || swap_before == BOOT_SWAP_TYPE_PERM) {
            const struct flash_area *sec;
            if (flash_area_open(FLASH_AREA_IMAGE_SECONDARY(0), &sec) == 0) {
                mboot_image_info_read(sec, &cand);
                if (update_layout_rejected(sec, &cand, swap_before)) {
                    swap_before = BOOT_SWAP_TYPE_NONE;
                }
            }
        }
        #endif

        struct boot_rsp rsp;
        FIH_DECLARE(fih_rc, FIH_FAILURE);
        FIH_CALL(boot_go, fih_rc, &rsp);
        if (FIH_EQ(fih_rc, FIH_SUCCESS)) {
            #if MBOOT_POLICY_SINGLE && defined(MCUBOOT_HW_ROLLBACK_PROT)
            if (mboot_validate_raise_counter() != 0) {
                MCUBOOT_LOG_ERR("security counter update failed");
                enter_recovery(REC_FAULT);
            }
            #endif
            log_after_boot_go(swap_before, &pre, &cand);
            if (!recovery) {
                mboot_port_deinit();
                mboot_port_jump(mboot_dev_base(rsp.br_flash_dev_id) + rsp.br_image_off + rsp.br_hdr->ih_hdr_size);
            }
            // Recovery was requested and the pending swap or revert is now complete: stay in the bootloader.
        } else {
            MCUBOOT_LOG_ERR("no bootable image");
            mboot_updatelog_append(LOG_NO_IMAGE, MBOOT_RES_ERR_NO_IMAGE, SRC_BOOT, NULL, 0);
            recover_primary();
            why = REC_NO_IMAGE;
            #if MBOOT_POLICY_SINGLE && MBOOT_FSLOAD_ENABLE
            // Retry after a power loss during a single-slot fsload. The element stream saved
            // in the intent area before the slot was overwritten requests the load again.
            if (mboot_intent_load(&req)) {
                req.mode = MBOOT_REQ_FSLOAD;
            }
            #endif
        }
    }

    #if MBOOT_FSLOAD_ENABLE
    if (req.mode == MBOOT_REQ_FSLOAD) {
        int r = mboot_fsload_run(req.elems, req.elems_len);
        // After a good fsload the new image is pending (swap) or in place (single): reset so
        // boot_go() starts clean. After a failed one the old image boots if there is one.
        if (r == MBOOT_RES_OK || primary_bootable()) {
            mboot_port_deinit();
            mboot_port_reset();
        }
        why = REC_FSLOAD_FAILED;
    }
    #endif

    enter_recovery(why);
}
