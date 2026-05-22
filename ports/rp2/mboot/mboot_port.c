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

// mboot_port.c — rp2 adapter implementing the mboot_api.h contract.
//
// Implements all 17 mboot_port_* callbacks for the rp2 port.
// Flash backend callbacks are in port_flash.c.
// Region table is in boards/<BOARD>/mboot_regions.c.

#include <stdbool.h>
#include <stdint.h>
#include <string.h>

#include "hardware/gpio.h"
#include "hardware/sync.h"
#include "hardware/watchdog.h"
#include "pico/unique_id.h"
#include "hardware/structs/scb.h"
#include "hardware/structs/watchdog.h"
#include "hardware/regs/addressmap.h"
#include "pico/stdlib.h"
#include "tusb.h"

#include "mboot_api.h"
#include "mboot_board.h"

// Persistent flag for double-tap detection. Placed in .uninitialized_data so
// it survives watchdog and soft resets (C runtime does not zero this section).
uint32_t mboot_dbl_tap_reg __attribute__((section(".uninitialized_data")));

// ---------------------------------------------------------------------------
// Lifecycle callbacks
// ---------------------------------------------------------------------------

void mboot_port_early_init(uint32_t *initial_r0) {
    // Read the magic value from watchdog scratch[0], which the application
    // writes to request programmatic entry into mboot.
    uint32_t scratch = watchdog_hw->scratch[0];
    *initial_r0 = scratch;

    // Clear scratch[0] immediately so a subsequent reset does not loop back
    // into the bootloader unintentionally.
    watchdog_hw->scratch[0] = 0;

    // Basic hardware init: clock defaults are set by pico-sdk's runtime_init
    // called before main().  Nothing else needed at this stage.
}

bool mboot_port_entry_forced(void) {
    #if MBOOT_BOOTPIN >= 0
    gpio_init(MBOOT_BOOTPIN);
    gpio_set_dir(MBOOT_BOOTPIN, GPIO_IN);
    gpio_pull_up(MBOOT_BOOTPIN);
    // Active-low: return true if the pin is held low.
    return !gpio_get(MBOOT_BOOTPIN);
    #else
    return false;
    #endif
}

int mboot_port_get_reset_mode(void) {
    // No boot pin: default to NORMAL so the app boots on every reset.
    // DFU entry is via the magic-key path (machine.bootloader()) or double-tap.
    #if MBOOT_BOOTPIN < 0
    return MBOOT_RESET_MODE_NORMAL;
    #else
    // Sample the boot pin for MBOOT_BOOTPIN_WINDOW_MS ms.  If it is held low
    // (active-low with pull-up) for the full window, stay in bootloader mode.
    // Otherwise jump to the application if one is present.
    #ifndef MBOOT_BOOTPIN_WINDOW_MS
    #define MBOOT_BOOTPIN_WINDOW_MS (500)
    #endif
    uint32_t t0 = time_us_32();
    while ((time_us_32() - t0) < (uint32_t)(MBOOT_BOOTPIN_WINDOW_MS * 1000)) {
        if (!mboot_port_button_pressed()) {
            // Button released before window elapsed — try normal boot.
            return MBOOT_RESET_MODE_NORMAL;
        }
    }
    return MBOOT_RESET_MODE_BOOTLOADER;
    #endif
}

void mboot_port_cleanup(int reset_mode) {
    (void)reset_mode;
    // Only call tud_disconnect() if the USB stack has been initialised.
    // On the early-jump path (app valid, no DFU mode) tusb_init() is never
    // called, and invoking tud_disconnect() before init clears USB hardware
    // registers that have not been brought up.
    if (tud_inited()) {
        tud_disconnect();
        tud_deinit(0);
    }
}

// Validate the application vector table before jumping.
//
// Returns true if both the initial SP and reset-handler PC look plausible.
// Ranges differ by silicon family:
//   RP2040 SRAM: 0x20000000 - 0x20040000 (256 KiB striped SRAM)
//   RP2350 SRAM: 0x20000000 - 0x20082000 (520 KiB = 0x82000 bytes)
// The application must reside in QSPI flash above the mboot reserved area,
// and the reset handler must have the Thumb bit set (bit 0 == 1).
// app_vt_addr — locate the Cortex-M vector table inside the application slot.
//
// The pico-sdk's standard linker scripts emit `.boot2` (the 256-byte XIP
// setup stub) as the FIRST section of the application's FLASH region.  On
// RP2040 the section is always present and 256 bytes; on RP2350 it is
// optional and may be zero bytes.  When mboot jumps to the application it
// expects a Cortex-M vector table at the jump address, but on RP2040 that
// address holds boot2 ARM assembly.  Probe both candidates (offset 0 = no
// preamble, offset 0x100 = 256-byte boot2 preamble) and return the address
// of the first that looks like a real vector table; 0 if neither matches.
static uint32_t app_vt_addr(void) {
    #if PICO_RP2350
    const uint32_t sram_end = 0x20082000u;
    #else
    const uint32_t sram_end = 0x20040000u;
    #endif
    const uint32_t candidates[] = {
        APPLICATION_ADDR,
        APPLICATION_ADDR + 0x100u,  // skip pico-sdk boot2 preamble
    };

    for (size_t i = 0; i < sizeof(candidates) / sizeof(candidates[0]); i++) {
        uint32_t base = candidates[i];
        uint32_t app_msp = *((const uint32_t *)base);
        uint32_t app_reset = *((const uint32_t *)(base + 4));

        // SP must point into SRAM (or exactly at the top, which is a valid
        // initial SP).
        if (app_msp < 0x20000000u || app_msp > sram_end) {
            continue;
        }
        // Reset handler must have the Thumb bit set and land in flash above
        // the mboot reserved area but below SRAM.
        if ((app_reset & 1u) == 0u) {
            continue;
        }
        uint32_t app_reset_addr = app_reset & ~1u;
        if (app_reset_addr < APPLICATION_ADDR || app_reset_addr >= 0x20000000u) {
            continue;
        }
        return base;
    }
    return 0;
}

bool mboot_port_app_valid(void) {
    return app_vt_addr() != 0;
}

void mboot_port_delay_ms(uint32_t ms) {
    busy_wait_us((uint64_t)ms * 1000u);
}

MBOOT_NORETURN void mboot_port_leave(int reset_mode) {
    save_and_disable_interrupts();

    uint32_t app_base = (reset_mode == MBOOT_RESET_MODE_NORMAL) ? app_vt_addr() : 0;
    if (app_base != 0) {
        // Relocate VTOR to the application's vector table, then branch.
        // On Cortex-M0+, load SP from app[0] and jump to app[1].
        // Use a two-register approach: r0 for SP, r1 for entry point, since
        // M0+ Thumb allows ldr into any lo register but bx only from lo regs.
        scb_hw->vtor = app_base;
        const uint32_t *vtable = (const uint32_t *)app_base;
        uint32_t sp = vtable[0];
        uint32_t pc = vtable[1];
        __asm__ volatile (
            "mov sp, %0  \n"   // set stack pointer
            "bx  %1      \n"   // branch to application reset handler
            :
            : "r" (sp), "r" (pc)
            : "memory"
            );
    }

    // Any other mode (BOOTLOADER, FSLOAD) or if app vector table looks bad:
    // trigger a watchdog reset.
    watchdog_reboot(0, 0, 0);

    // Unreachable; silence compiler.
    for (;;) {
    }
}

// ---------------------------------------------------------------------------
// USB identity callbacks
// ---------------------------------------------------------------------------

void mboot_port_get_serial_number(char *buf, size_t buf_len) {
    pico_get_unique_board_id_string(buf, (uint)buf_len);
}

const char *mboot_port_get_product_string(void) {
    return MBOOT_PRODUCT_STRING;
}

uint16_t mboot_port_get_vid(void) {
    return MBOOT_USB_VID;
}

uint16_t mboot_port_get_pid(void) {
    return MBOOT_USB_PID;
}

// ---------------------------------------------------------------------------
// UI callbacks
// ---------------------------------------------------------------------------

void mboot_port_led_init(void) {
    #if MBOOT_LED_PIN >= 0
    gpio_init(MBOOT_LED_PIN);
    gpio_set_dir(MBOOT_LED_PIN, GPIO_OUT);
    gpio_put(MBOOT_LED_PIN, 0);
    #endif
}

void mboot_port_led_set(unsigned int led_mask) {
    #if MBOOT_LED_PIN >= 0
    gpio_put(MBOOT_LED_PIN, (led_mask & 1u) ? 1 : 0);
    #else
    (void)led_mask;
    #endif
}

bool mboot_port_button_pressed(void) {
    #if MBOOT_BOOTPIN >= 0
    return !gpio_get(MBOOT_BOOTPIN);
    #else
    return false;
    #endif
}
