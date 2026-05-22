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

// mboot_port.c — mimxrt port adapter implementing all mboot_port_* callbacks.
//
// Implements the 17 extern functions declared in mboot_api.h for the MIMXRT
// family.  Board-specific constants (VID/PID, flash base, product string) come
// from mboot_board.h, which is selected by the board-level include path.
//
// Dependency on MicroPython headers: none.  This file includes only NXP SDK
// headers, CMSIS core headers, and mboot_api.h.

#include <string.h>
#include <stdint.h>

#include "fsl_common.h"
#include "fsl_clock.h"
#include "fsl_gpio.h"
#include "fsl_iomuxc.h"
#include "fsl_wdog.h"

#include CLOCK_CONFIG_H

#include "tusb.h"

#include "mboot_api.h"
#include "mboot_board.h"

// ---------------------------------------------------------------------------
// Linker symbols from the board linker script.
// ---------------------------------------------------------------------------

// __mboot_end is the start of the application's vector table; its value is
// (flash_start + mboot_size).  mboot_port_leave() jumps to this address.
extern uint32_t __mboot_end;

// ---------------------------------------------------------------------------
// USB interrupt handlers
//
// TinyUSB's dcd_ci_hs.c only provides dcd_int_handler(); the named ISR that
// wires it into the Cortex-M vector table must be provided by the port.  The
// BSP equivalent is hw/bsp/imxrt/family.c which is not compiled in mboot.
// ---------------------------------------------------------------------------

void USB_OTG1_IRQHandler(void) {
    tusb_int_handler(0, true);
}

#if defined(MBOOT_MCU_MIMXRT11XX) && MBOOT_MCU_MIMXRT11XX
void USB_OTG2_IRQHandler(void) {
    tusb_int_handler(1, true);
}
#endif

// ---------------------------------------------------------------------------
// Lifecycle callbacks
// ---------------------------------------------------------------------------


// mboot_port_init_caches — configure CPU caches and FlexSPI AHB buffering.
//
// SystemInit() (called by startup assembly before main) enables both the
// Cortex-M7 I-Cache and D-Cache.  mboot keeps the I-Cache enabled so that
// instruction fetches during USB interrupt handling are served from cache
// rather than the FlexSPI AHB bus.  Serving fetches from AHB while USB DMA
// is also on the bus causes AHB contention; under specific timing the
// FlexSPI returns 0xFFFF for the in-flight instruction fetch, producing an
// UNDEFINSTR HardFault.
//
// The D-Cache is disabled because mboot does not call SCB_CleanDCache before
// issuing FlexSPI SPI-master flash-write commands; leaving it enabled risks
// dirty cache lines making DMA-written data stale.
//
// FlexSPI AHBCR: CACHABLEEN is set so the AHB RX buffer services cacheable
// reads (I-Cache miss fills) and can absorb AHB bus contention from USB DMA.
// PREFETCHEN is cleared to avoid speculative prefetch stealing bus bandwidth.
// CLRAHBRXBUF flushes any stale data already in the buffer.
//
// Must run from ITCM: disabling the D-Cache while executing from flash is safe,
// but the function also manipulates FlexSPI registers, so ITCM placement keeps
// it off the critical AHB path entirely.
__attribute__((noinline, section(".ram_functions")))
static void mboot_port_init_caches(void) {
    // Enable I-Cache so instruction fetches during USB interrupt handling are
    // served from cache rather than the FlexSPI AHB bus.  Without I-Cache,
    // every instruction fetch is a separate AHB read; concurrent USB DMA on the
    // same AHB matrix causes the FlexSPI to return 0xFFFF for in-flight
    // instruction fetches, producing an UNDEFINSTR HardFault.
    //
    // The Boot ROM and mboot startup do NOT enable the I-Cache, so we do it
    // here explicitly.  This function runs from ITCM so enabling I-Cache does
    // not affect the currently executing instructions.
    __DSB();
    __ISB();
    SCB->ICIALLU = 0U;  // Invalidate any stale lines before enabling.
    __DSB();
    __ISB();
    SCB->CCR |= SCB_CCR_IC_Msk;  // Enable I-Cache.
    __DSB();
    __ISB();

    // Disable D-Cache: mboot does not flush D-Cache before FlexSPI SPI-master
    // writes, so leaving it enabled risks dirty lines shadowing DMA data.
    SCB->CCR &= ~SCB_CCR_DC_Msk;
    __DSB();

    // Configure FlexSPI AHB buffering.
    //
    // CACHABLEEN=1: when the I-Cache fills a cache line from FlexSPI, the AHB
    // transaction carries HPROT[1]=1 (cacheable).  With CACHABLEEN=1 the FlexSPI
    // stores the SPI NOR read result in its internal AHB RX buffer and can serve
    // the CPU even if USB DMA has won the AHB bus in between.  With CACHABLEEN=0
    // the buffer is bypassed, every cache-miss goes directly to SPI NOR with no
    // intermediate storage; if USB DMA wins the bus mid-fetch the FlexSPI cannot
    // complete the response and the I-Cache stores garbage into the cache line,
    // causing an UNDEFINSTR when the CPU executes the corrupted instruction.
    // BOARD_BootClockRUN() leaves CACHABLEEN=0, so we must set it explicitly.
    //
    // PREFETCHEN=0: suppress speculative AHB prefetch so the FlexSPI does not
    // consume bus bandwidth that could delay CPU instruction-fetch cache fills.
    //
    // CLRAHBRXBUF: flush any data already in the AHB RX buffer from before this
    // init so the I-Cache does not warm from stale pre-reset content.
    uint32_t ahbcr = FLEXSPI->AHBCR;
    ahbcr &= ~FLEXSPI_AHBCR_PREFETCHEN_MASK;
    ahbcr |= (FLEXSPI_AHBCR_CACHABLEEN_MASK | FLEXSPI_AHBCR_CLRAHBRXBUF_MASK);
    FLEXSPI->AHBCR = ahbcr;
    __DSB();
    __ISB();
}

// mboot_port_early_init — configure clocks and read programmatic entry magic.
//
// The NXP startup_*.S calls SystemInit then main.  By the time we get here the
// system clock is already running from the ROM default; we call the board clock
// config to switch to the application-level frequencies.
//
// Programmatic entry: the application writes MBOOT_INITIAL_R0_KEY to
// SNVS->LPGPR[3] before triggering a watchdog reset (see ports/mimxrt/
// modmachine.c:89-90).  We read that register here and clear it so it does not
// persist across subsequent resets.
void mboot_port_early_init(uint32_t *initial_r0) {
    // Board clock configuration (generated by SDK, lives in
    // boards/$(MCU_SERIES)_clock_config.c).
    BOARD_BootClockRUN();

    // Configure CPU caches and FlexSPI AHB buffer.  Must run from ITCM;
    // see mboot_port_init_caches().
    mboot_port_init_caches();

    // Read the retention-RAM magic and clear it.
    uint32_t magic = SNVS->LPGPR[3];
    SNVS->LPGPR[3] = 0;
    if (initial_r0 != NULL) {
        *initial_r0 = magic;
    }
}

bool mboot_port_entry_forced(void) {
    #ifdef MBOOT_BOARD_USER_BUTTON_GPIO
    CCM->CCGR0 |= (3UL << CCM_CCGR0_CG15_SHIFT);
    IOMUXC_SetPinMux(MBOOT_BOARD_USER_BUTTON_IOMUXC, 0U);
    IOMUXC_SetPinConfig(MBOOT_BOARD_USER_BUTTON_IOMUXC, 0x0001B0A0U);
    MBOOT_BOARD_USER_BUTTON_GPIO->GDIR &= ~(1UL << MBOOT_BOARD_USER_BUTTON_PIN);
    // ISB serialises the peripheral AHB read against the CM7 speculative
    // instruction prefetch from XIP flash; without it they race on the AHB
    // crossbar and can trigger an UNDEFINSTR HardFault.
    __ISB();
    return (MBOOT_BOARD_USER_BUTTON_GPIO->DR & (1UL << MBOOT_BOARD_USER_BUTTON_PIN)) == 0U;
    #else
    return false;
    #endif
}

// mboot_port_usb_init — enable USB OTG1 clock gate, configure PLL_USB1 to
// 480 MHz, power up the USB PHY, and apply per-board PHY trim overrides.
//
// This mirrors ports/mimxrt/board_init.c:usb_phy0_init (lines 111-131) but
// is called from the mboot main loop immediately before tusb_init().
// If a board needs a non-default sequence it can define MBOOT_BOARD_USB_INIT()
// in mboot_board.h and that macro will be called instead.
//
// The PHY trim values (D_CAL=0x0C, TXCAL45DP=0x06, TXCAL45DN=0x06) match
// the TinyUSB BSP reference values for MIMXRT10xx boards.
#ifndef MBOOT_BOARD_USB_INIT
static void mboot_port_usb_phy_init(void) {
    #ifdef USBPHY1
    USBPHY_Type *usb_phy = USBPHY1;
    #else
    USBPHY_Type *usb_phy = USBPHY;
    #endif

    // Enable USB HS clock (USB1 PLL to 480 MHz) and the USB HS PHY PLL.
    CLOCK_EnableUsbhs0PhyPllClock(kCLOCK_Usbphy480M, BOARD_XTAL0_CLK_HZ);
    CLOCK_EnableUsbhs0Clock(kCLOCK_Usb480M, BOARD_XTAL0_CLK_HZ);

    // Enable PHY support for Low Speed devices and LS-via-FS-Hub.
    // Without these two UTMI level bits the PHY will not respond to setup
    // packets, causing EPIPE errors when the host reads the device descriptor.
    usb_phy->CTRL |= USBPHY_CTRL_SET_ENUTMILEVEL2_MASK | USBPHY_CTRL_SET_ENUTMILEVEL3_MASK;

    // RT117x requires explicit PHY trim override before powering up.
    #if defined(MIMXRT117x_SERIES)
    usb_phy->TRIM_OVERRIDE_EN =
        USBPHY_TRIM_OVERRIDE_EN_TRIM_DIV_SEL_OVERRIDE(1) |
        USBPHY_TRIM_OVERRIDE_EN_TRIM_ENV_TAIL_ADJ_VD_OVERRIDE(1) |
        USBPHY_TRIM_OVERRIDE_EN_TRIM_TX_D_CAL_OVERRIDE(1) |
        USBPHY_TRIM_OVERRIDE_EN_TRIM_TX_CAL45DP_OVERRIDE(1) |
        USBPHY_TRIM_OVERRIDE_EN_TRIM_TX_CAL45DN_OVERRIDE(1);
    #endif

    // Power up PHY (clear all PWD bits to normal operation).
    usb_phy->PWD = 0U;

    // TX timing: D_CAL=0x0C matches the TinyUSB BSP reference values.
    usb_phy->TX = (usb_phy->TX &
        ~(USBPHY_TX_D_CAL_MASK | USBPHY_TX_TXCAL45DM_MASK | USBPHY_TX_TXCAL45DP_MASK)) |
        USBPHY_TX_D_CAL(0x0CU) | USBPHY_TX_TXCAL45DP(0x06U) | USBPHY_TX_TXCAL45DM(0x06U);
}
#endif // MBOOT_BOARD_USB_INIT

void mboot_port_usb_init(void) {
    #ifdef MBOOT_BOARD_USB_INIT
    MBOOT_BOARD_USB_INIT();
    #else
    mboot_port_usb_phy_init();
    #endif
}

// mboot_port_app_valid — check application vector table before jumping.
//
// Returns true if the app MSP looks like a valid SRAM pointer and the reset
// handler has the Thumb bit set and lands in flash above the mboot slot.
// Uses MBOOT_FLASH_BASE and MICROPY_HW_FLASH_SIZE from the board header.
bool mboot_port_app_valid(void) {
    uint32_t app_base = (uint32_t)&__mboot_end;
    uint32_t app_msp = *(const uint32_t *)app_base;
    uint32_t app_reset = *(const uint32_t *)(app_base + 4u);

    // SP must be in SRAM: above 0x20000000 and below flash start.
    if (app_msp < 0x20000000u || app_msp > MBOOT_FLASH_BASE) {
        return false;
    }

    // Reset handler must have Thumb bit set and land in flash above mboot.
    if ((app_reset & 1u) == 0u) {
        return false;
    }
    uint32_t app_reset_addr = app_reset & ~1u;
    if (app_reset_addr < app_base ||
        app_reset_addr >= MBOOT_FLASH_BASE + MICROPY_HW_FLASH_SIZE) {
        return false;
    }

    return true;
}

// mboot_port_delay_ms — busy-wait using the NXP SDK delay primitive.
void mboot_port_delay_ms(uint32_t ms) {
    SDK_DelayAtLeastUs(ms * 1000u, SystemCoreClock);
}

// mboot_port_get_reset_mode — boot the app by default.
//
// The board has no UI for selecting a reset mode; DFU entry is via double-tap
// or machine.bootloader() (MBOOT_INITIAL_R0_KEY).
int mboot_port_get_reset_mode(void) {
    return MBOOT_RESET_MODE_NORMAL;
}

// mboot_port_cleanup — disconnect USB and restore a minimal clock state.
//
// Called on the leave path even when the fast-boot path skipped tud_init()
// (no DFU session was requested).  Guard tud_disconnect/tud_deinit with
// tud_inited() because tud_disconnect dereferences _usbd_rhport which is
// RHPORT_INVALID (0xFF) until tud_init has run; calling it before init
// faults inside dcd_disconnect.
void mboot_port_cleanup(int reset_mode) {
    (void)reset_mode;
    if (tud_inited()) {
        tud_disconnect();
        tud_deinit(0);
    }
}

// mboot_port_leave_to_app — naked trampoline for jumping to the application.
//
// Called with r0 = application vector table base address (__mboot_end value).
// Relocates VTOR, loads initial MSP, and branches to the reset handler.
// Marked naked so no compiler prologue can clobber r0 before the asm runs.
static __attribute__((naked)) void mboot_port_leave_to_app(uint32_t app_base) {
    (void)app_base; // in r0
    __asm volatile (
        "   cpsid i                    \n"
        // r1 = app_base (already in r0 by AAPCS, copy to r1 for clarity).
        "   mov  r1, r0                \n"
        // Set VTOR to the application vector table.
        "   ldr  r2, =0xE000ED08       \n"  // SCB->VTOR
        "   str  r1, [r2]              \n"
        "   dsb                        \n"
        "   isb                        \n"
        // Load new MSP from first word of app vector table.
        "   ldr  r2, [r1, #0]          \n"
        "   msr  msp, r2               \n"
        // Branch to reset handler (second word of app vector table).
        "   ldr  r3, [r1, #4]          \n"
        "   bx   r3                    \n"
        :
        :
        : "r1", "r2", "r3", "memory"
        );
    // Unreachable; suppress compiler warning.
    __builtin_unreachable();
}

// mboot_port_leave — jump to the application or reset the MCU.
//
// For MBOOT_RESET_MODE_NORMAL: call mboot_port_leave_to_app with the value of
// __mboot_end (the start of the application vector table).
//
// For all other modes: trigger a watchdog software reset via WDOG1.
void mboot_port_leave(int reset_mode) {
    if (reset_mode == MBOOT_RESET_MODE_NORMAL && mboot_port_app_valid()) {
        mboot_port_leave_to_app((uint32_t)&__mboot_end);
        // Unreachable.
    }
    // Any other mode, or if app vector table looks invalid: reset via watchdog.
    WDOG_TriggerSystemSoftwareReset(WDOG1);
    // Unreachable, but the compiler cannot prove it.
    for (;;) {
    }
}

// ---------------------------------------------------------------------------
// USB identity callbacks
// ---------------------------------------------------------------------------

void mboot_port_get_serial_number(char *buf, size_t buf_len) {
    // Read the 64-bit unique ID from OCOTP shadow registers (Bank0 Word1/Word2).
    // CFG0 (offset 0x410) = low 32 bits, CFG1 (offset 0x420) = high 32 bits.
    // Shadow registers are readable without driver initialisation; the ROM has
    // already enabled the OCOTP clock before transferring control to mboot.
    uint32_t uid_lo = OCOTP->CFG0;
    uint32_t uid_hi = OCOTP->CFG1;
    // Format as 16 uppercase hex digits: high word first.
    // Use arithmetic rather than a flash table to avoid FlexSPI LDRB issues.
    char tmp[17];
    for (int i = 0; i < 8; i++) {
        uint32_t hi_nib = (uid_hi >> (28 - i * 4)) & 0xFu;
        uint32_t lo_nib = (uid_lo >> (28 - i * 4)) & 0xFu;
        tmp[i] = (char)(hi_nib < 10u ? '0' + hi_nib : 'A' + hi_nib - 10u);
        tmp[i + 8] = (char)(lo_nib < 10u ? '0' + lo_nib : 'A' + lo_nib - 10u);
    }
    tmp[16] = '\0';
    size_t n = 16;
    if (n >= buf_len) {
        n = buf_len - 1;
    }
    memcpy(buf, tmp, n);
    buf[n] = '\0';
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

// No LED or button is wired on the supported mimxrt boards; the stubs below
// satisfy the mboot_api.h contract without driving any GPIO.

void mboot_port_led_init(void) {
}

void mboot_port_led_set(unsigned int led_mask) {
    (void)led_mask;
}

bool mboot_port_button_pressed(void) {
    return false;
}
