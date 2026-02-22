/*
 * This file is part of the MicroPython project, http://micropython.org/
 *
 * SPDX-License-Identifier: Apache-2.0
 *
 * nRF52840 clock control for Zephyr BLE controller without full Zephyr OS.
 *
 * Replaces Zephyr's lll_clock.c which depends on the onoff_manager and
 * nrf_clock_control driver. We drive the nRF52840 HFXO and LFXO directly
 * via hardware registers.
 */

#include <stdint.h>
#include <stdbool.h>
#include <errno.h>

#if MICROPY_BLUETOOTH_ZEPHYR_CONTROLLER

#include <nrf.h>

#include "hal/debug.h"

// SCA (Sleep Clock Accuracy) lookup table — PPM values for each SCA index
// SCA 0 = 500ppm, SCA 7 = 20ppm (BT Core Spec Vol 6, Part B, 4.2.2)
static uint16_t const sca_ppm_lut[] = {500, 250, 150, 100, 75, 50, 30, 20};

static atomic_t hf_refcnt;

// nRF52840 DK uses a 32.768 kHz LFXO with ±20ppm accuracy (SCA 7)
// This may need to be made configurable per-board.
#ifndef CLOCK_CONTROL_NRF_K32SRC_ACCURACY
#define CLOCK_CONTROL_NRF_K32SRC_ACCURACY 7
#endif

int lll_clock_init(void)
{
    // The BLE controller requires LFXO (external 32.768kHz crystal) for
    // accurate radio timing. The nRF port's mp_nrf_start_lfclk() may have
    // already started LFCLK with the default source (LFRC internal RC,
    // ±2% = 20000ppm uncalibrated). If so, stop and restart with LFXO.
    // The SCA config claims 20ppm accuracy which is only valid for LFXO.

    bool running = (NRF_CLOCK->LFCLKSTAT & CLOCK_LFCLKSTAT_STATE_Msk) != 0;
    bool is_xtal = (NRF_CLOCK->LFCLKSTAT & CLOCK_LFCLKSTAT_SRC_Msk) ==
                   (CLOCK_LFCLKSTAT_SRC_Xtal << CLOCK_LFCLKSTAT_SRC_Pos);

    if (running && is_xtal) {
        return 0;  // Already running on LFXO
    }

    if (running) {
        // Running on LFRC — must stop before changing source
        NRF_CLOCK->TASKS_LFCLKSTOP = 1;
        // Per nRF52840 PS v1.1 §6.4.4: wait for clock to stop
        while (NRF_CLOCK->LFCLKSTAT & CLOCK_LFCLKSTAT_STATE_Msk) {
        }
    }

    // Select LFXO as source and start
    NRF_CLOCK->LFCLKSRC = (CLOCK_LFCLKSRC_SRC_Xtal << CLOCK_LFCLKSRC_SRC_Pos);
    NRF_CLOCK->EVENTS_LFCLKSTARTED = 0;
    NRF_CLOCK->TASKS_LFCLKSTART = 1;

    return 0;
}

int lll_clock_deinit(void)
{
    // Don't stop LFCLK — it's shared with other subsystems (RTC)
    return 0;
}

int lll_clock_wait(void)
{
    // Wait for LFXO to be running and stable.
    // Cannot use a static "done" flag because lll_clock_init() may have
    // restarted LFCLK with LFXO after the nRF port started it on LFRC.
    while (NRF_CLOCK->EVENTS_LFCLKSTARTED == 0) {
        __WFE();
    }

    return 0;
}

int lll_hfclock_on(void)
{
    if (__atomic_fetch_add(&hf_refcnt, 1, __ATOMIC_SEQ_CST) > 0) {
        return 0;
    }

    NRF_CLOCK->EVENTS_HFCLKSTARTED = 0;
    NRF_CLOCK->TASKS_HFCLKSTART = 1;
    DEBUG_RADIO_XTAL(1);

    return 0;
}

int lll_hfclock_on_wait(void)
{
    __atomic_fetch_add(&hf_refcnt, 1, __ATOMIC_SEQ_CST);

    NRF_CLOCK->EVENTS_HFCLKSTARTED = 0;
    NRF_CLOCK->TASKS_HFCLKSTART = 1;

    while (NRF_CLOCK->EVENTS_HFCLKSTARTED == 0) {
        __WFE();
    }

    DEBUG_RADIO_XTAL(1);

    return 0;
}

int lll_hfclock_off(void)
{
    int prev = __atomic_fetch_add(&hf_refcnt, -1, __ATOMIC_SEQ_CST);

    if (prev < 1) {
        // Correct the underflow
        __atomic_fetch_add(&hf_refcnt, 1, __ATOMIC_SEQ_CST);
        return -EALREADY;
    }

    if (prev > 1) {
        return 0;
    }

    NRF_CLOCK->TASKS_HFCLKSTOP = 1;
    DEBUG_RADIO_XTAL(0);

    return 0;
}

uint8_t lll_clock_sca_local_get(void)
{
    return CLOCK_CONTROL_NRF_K32SRC_ACCURACY;
}

uint32_t lll_clock_ppm_local_get(void)
{
    return sca_ppm_lut[CLOCK_CONTROL_NRF_K32SRC_ACCURACY];
}

uint32_t lll_clock_ppm_get(uint8_t sca)
{
    return sca_ppm_lut[sca];
}

#endif // MICROPY_BLUETOOTH_ZEPHYR_CONTROLLER
