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

#include <errno.h>
#include <string.h>

#include "mboot_port.h"
#if defined(MICROPY_HW_MBOOT_APP) && MICROPY_HW_MBOOT_APP
#include "mphalport.h"
#endif
#include "mboot_dev.h"
#include "mboot_led.h"

#include "flash.h"
#include "fsl_clock.h"
#include "fsl_wdog.h"
#include "fsl_device_registers.h"
#include "mimxrt_reset_cause.h"
#include "mimxrt_usb_phy.h"
#include CLOCK_CONFIG_H
#include CPU_HEADER_H

#if defined(MIMXRT117x_SERIES)
#include "fsl_soc_src.h"
#else
#include "fsl_src.h"
#endif

#if defined(MBOOT_ROLE_BOOTLOADER) && MBOOT_ROLE_BOOTLOADER
#include "tusb.h"
#endif

extern uint8_t MBOOT_REQ_START[];
extern uint8_t MBOOT_BACKUP_START[];

#define MBOOT_REQUEST_BYTES (0x400u)
#define MBOOT_BACKUP_WORDS (4u)
#define MBOOT_USB_RHPORT (0u)

static volatile uint32_t mboot_ticks;
static uint32_t mboot_initial_reset_raw;
static uint32_t mboot_initial_wdog_flags;
static uint32_t mboot_initial_reset_cause;
static bool mboot_initial_reset_consumed;

void mimxrt_wdt_feed_aux(void) __attribute__((section(".ram_functions")));

static uint32_t mboot_reset_raw(void) {
    return SRC_GetResetStatusFlags(SRC);
}

static uint32_t mboot_reset_cause_from_flags(uint32_t raw, uint32_t wdog_flags) {
    switch (mimxrt_reset_cause_classify(raw, wdog_flags)) {
        case MIMXRT_RESET_CAUSE_POR:
            return MBOOT_RESET_POR;
        case MIMXRT_RESET_CAUSE_WATCHDOG:
            return MBOOT_RESET_WDT;
        case MIMXRT_RESET_CAUSE_PIN:
            return MBOOT_RESET_PIN;
        case MIMXRT_RESET_CAUSE_SOFTWARE:
            return MBOOT_RESET_SOFT;
        case MIMXRT_RESET_CAUSE_OVERVOLTAGE:
            return MBOOT_RESET_POR;
        default:
            return MBOOT_RESET_OTHER;
    }
}

static void mboot_reset_flags_clear(uint32_t raw) {
    #if defined(MIMXRT117x_SERIES)
    SRC_ClearGlobalSystemResetStatus(SRC, raw);
    #else
    SRC_ClearResetStatusFlags(SRC, raw);
    #endif
}

static void mboot_port_wdog_disable(WDOG_Type *wdog) {
    wdog->WMCR &= ~(uint16_t)WDOG_WMCR_PDE_MASK;
    if (wdog->WCR & WDOG_WCR_WDE_MASK) {
        wdog->WCR &= ~(uint16_t)WDOG_WCR_WDE_MASK;
    }
}

static void mboot_retained_clear(void) {
    volatile uint32_t *backup = (volatile uint32_t *)MBOOT_BACKUP_START;
    volatile uint32_t *request = (volatile uint32_t *)MBOOT_REQ_START;
    for (size_t i = 0; i < MBOOT_BACKUP_WORDS; ++i) {
        backup[i] = 0;
    }
    for (size_t i = 0; i < MBOOT_REQUEST_BYTES; ++i) {
        ((volatile uint8_t *)request)[i] = 0;
    }
    if (SCB->CCR & SCB_CCR_DC_Msk) {
        SCB_CleanDCache();
    }
    __DSB();
}

void mboot_port_early_init(void) {
    #if defined(MIMXRT117x_SERIES)
    // Keep the auxiliary M4 from fetching XIP while the M7 updates flash.
    SRC->SCR &= ~SRC_SCR_BT_RELEASE_M4_MASK;
    __DSB();
    #endif
    uint32_t src_reset_flags = mboot_reset_raw() & 0xffffu;
    mboot_initial_wdog_flags = WDOG_GetStatusFlags(WDOG1) | WDOG_GetStatusFlags(WDOG2);
    mboot_initial_reset_raw = src_reset_flags |
        ((mboot_initial_wdog_flags & (kWDOG_PowerOnResetFlag | kWDOG_TimeoutResetFlag | kWDOG_SoftwareResetFlag)) << 16);
    // Keep both reset-status registers in the shared raw reset snapshot.
    mboot_initial_reset_cause = mboot_reset_cause_from_flags(src_reset_flags, mboot_initial_wdog_flags);
    mboot_initial_reset_consumed = false;

    if (mboot_initial_reset_cause == MBOOT_RESET_POR) {
        mboot_retained_clear();
    }
    mboot_reset_flags_clear(src_reset_flags);
    // Preserve the incoming watchdog state in the reset snapshot, then match the
    // normal startup policy for the legacy watchdogs. RTWDOG instances stay enabled.
    mboot_port_wdt_feed();
    mboot_port_wdog_disable(WDOG1);
    mboot_port_wdog_disable(WDOG2);

    // Complete outstanding ROM/XIP prefetches before their clock can change.
    while (!FLEXSPI_GetBusIdleStatus(BOARD_FLEX_SPI)) {
    }
    BOARD_InitBootClocks();
    // Establish the operating flash root before any code returns to XIP.
    flexspi_update_clk(MICROPY_HW_FLASH_CLK);
    SystemCoreClockUpdate();
    SysTick_Config(SystemCoreClock / 1000u);
    NVIC_SetPriority(SysTick_IRQn, 0xffu);
    SCB_EnableICache();
    SCB_EnableDCache();
    __enable_irq();
}

void mboot_port_deinit(void) {
    #if defined(MBOOT_ROLE_BOOTLOADER) && MBOOT_ROLE_BOOTLOADER
    mboot_port_usb_deinit();
    #endif
    SysTick->CTRL = 0;
    SCB->ICSR = SCB_ICSR_PENDSTCLR_Msk | SCB_ICSR_PENDSVCLR_Msk;
    mboot_fault_disable();
    mboot_fault_count_clear();
    if (SCB->CCR & SCB_CCR_DC_Msk) {
        SCB_CleanDCache();
        SCB_DisableDCache();
    }
    SCB_DisableICache();
    __DSB();
    __ISB();
    mimxrt_wdt_feed_aux();
}

MBOOT_NORETURN void mboot_port_reset(void) __attribute__((section(".ram_functions")));
MBOOT_NORETURN void mboot_port_reset(void) {
    __disable_irq();
    flash_wait_for_idle();
    if (SCB->CCR & SCB_CCR_DC_Msk) {
        SCB_CleanDCache();
    }
    __DSB();
    __ISB();
    #if defined(MIMXRT117x_SERIES)
    // machine.reset() resets the RT117x through WDOG1, which is also the reset used here.
    WDOG_TriggerSystemSoftwareReset(WDOG1);
    #else
    NVIC_SystemReset();
    #endif
    for (;;) {
    }
}

uint32_t mboot_port_reset_cause(void) {
    if (!mboot_initial_reset_consumed) {
        mboot_initial_reset_consumed = true;
        mboot_reset_flags_stash(mboot_initial_reset_raw, mboot_initial_reset_cause);
        return mboot_initial_reset_cause;
    }
    return MBOOT_RESET_OTHER;
}

uint32_t mboot_port_ticks_ms(void) {
    return mboot_ticks;
}

void mboot_port_delay_ms(uint32_t ms) {
    uint32_t start = mboot_port_ticks_ms();
    while ((uint32_t)(mboot_port_ticks_ms() - start) < ms) {
        mboot_port_wdt_feed();
    }
}

static void mboot_port_wdt_feed_rtwdog(RTWDOG_Type *wdog) __attribute__((section(".ram_functions")));
static void mboot_port_wdt_feed_rtwdog(RTWDOG_Type *wdog) {
    uint32_t cs = wdog->CS;
    if ((cs & RTWDOG_CS_EN_MASK) == 0) {
        return;
    }
    if ((cs & RTWDOG_CS_WIN_MASK) && ((wdog->CNT & 0xffffu) < (wdog->WIN & 0xffffu))) {
        return;
    }
    if (cs & RTWDOG_CS_CMD32EN_MASK) {
        wdog->CNT = RTWDOG_REFRESH_KEY;
    } else {
        wdog->CNT = RTWDOG_REFRESH_KEY & 0xffffu;
        wdog->CNT = RTWDOG_REFRESH_KEY >> 16;
    }
}

static void mboot_port_wdt_feed_internal(uint32_t feed_wdog1) __attribute__((section(".ram_functions")));
static void mboot_port_wdt_feed_internal(uint32_t feed_wdog1) {
    uint32_t primask = __get_PRIMASK();
    __disable_irq();
    if (feed_wdog1 && (WDOG1->WCR & WDOG_WCR_WDE_MASK)) {
        WDOG1->WSR = (uint16_t)(WDOG_REFRESH_KEY & 0xffffu);
        WDOG1->WSR = (uint16_t)(WDOG_REFRESH_KEY >> 16);
    }
    if (WDOG2->WCR & WDOG_WCR_WDE_MASK) {
        WDOG2->WSR = (uint16_t)(WDOG_REFRESH_KEY & 0xffffu);
        WDOG2->WSR = (uint16_t)(WDOG_REFRESH_KEY >> 16);
    }
    #if defined(MIMXRT117x_SERIES)
    mboot_port_wdt_feed_rtwdog(RTWDOG3);
    mboot_port_wdt_feed_rtwdog(RTWDOG4);
    #else
    mboot_port_wdt_feed_rtwdog(RTWDOG);
    #endif
    __DSB();
    __set_PRIMASK(primask);
}

void mimxrt_wdt_feed_aux(void) {
    mboot_port_wdt_feed_internal(0);
}

void mboot_port_wdt_feed(void) __attribute__((section(".ram_functions")));
void mboot_port_wdt_feed(void) {
    mboot_port_wdt_feed_internal(1);
}

bool mboot_port_entry_forced(void) {
    #if defined(MICROPY_HW_MBOOT_ENTRY_FORCED)
    return MICROPY_HW_MBOOT_ENTRY_FORCED();
    #else
    return false;
    #endif
}


uint32_t mboot_port_bkp_read(unsigned idx) {
    if (idx >= MBOOT_BACKUP_WORDS) {
        return 0;
    }
    return ((volatile uint32_t *)MBOOT_BACKUP_START)[idx];
}

void mboot_port_bkp_write(unsigned idx, uint32_t value) {
    if (idx < MBOOT_BACKUP_WORDS) {
        ((volatile uint32_t *)MBOOT_BACKUP_START)[idx] = value;
    }
}

void *mboot_port_request_ram(size_t *size_out) {
    if (size_out != NULL) {
        *size_out = MBOOT_REQUEST_BYTES;
    }
    return MBOOT_REQ_START;
}

int mboot_port_flash_dev_init(void) {
    flash_init();
    return 0;
}

int mboot_port_flash_dev_read(uint8_t dev, uint32_t off, void *dst, uint32_t len) {
    if (dev != 0) {
        return -EINVAL;
    }
    flash_read_block(off, dst, len);
    return 0;
}

static int mboot_flash_result(status_t status) {
    if (status == kStatus_Success) {
        return 0;
    }
    return status == kStatus_Busy ? -EBUSY : -EIO;
}

int mboot_port_flash_dev_write(uint8_t dev, uint32_t off, const void *src, uint32_t len) {
    if (dev != 0 || (off & 3u) != 0 || (len & 3u) != 0) {
        return -EINVAL;
    }
    return mboot_flash_result(flash_write_block(off, src, len));
}

int mboot_port_flash_dev_erase(uint8_t dev, uint32_t off, uint32_t len) {
    if (dev != 0 || (off % MICROPY_HW_MBOOT_DEV0_ERASE) != 0 || (len % MICROPY_HW_MBOOT_DEV0_ERASE) != 0) {
        return -EINVAL;
    }
    while (len != 0) {
        int ret = mboot_flash_result(flash_erase_sector(off));
        if (ret != 0) {
            return ret;
        }
        off += MICROPY_HW_MBOOT_DEV0_ERASE;
        len -= MICROPY_HW_MBOOT_DEV0_ERASE;
    }
    return 0;
}

int mboot_port_flash_cache_prepare(void) {
    if (SCB->CCR & SCB_CCR_DC_Msk) {
        SCB_CleanInvalidateDCache();
    }
    SCB_InvalidateICache();
    __DSB();
    __ISB();
    return 0;
}

int mboot_port_flash_erase_sector(uint32_t off) {
    if (off % MICROPY_HW_MBOOT_DEV0_ERASE != 0) {
        return -EINVAL;
    }
    return mboot_flash_result(flash_erase_sector(off));
}

int mboot_port_flash_program_unit(uint32_t off, const uint32_t *w) {
    if ((off & (MICROPY_HW_MBOOT_DEV0_WRITE - 1u)) != 0) {
        return -EINVAL;
    }
    return mboot_flash_result(flash_write_block(off, (const uint8_t *)w, MICROPY_HW_MBOOT_DEV0_WRITE));
}

void mboot_port_usb_serial_number(char *buf, size_t len) {
    static const char hex[] = "0123456789ABCDEF";
    uint32_t uid[2];
    #if defined(MIMXRT117x_SERIES)
    uid[0] = OCOTP->FUSEN[0x10].FUSE;
    uid[1] = OCOTP->FUSEN[0x11].FUSE;
    #else
    uid[0] = OCOTP->CFG0;
    uid[1] = OCOTP->CFG1;
    #endif
    size_t pos = 0;
    for (size_t word = 0; word < 2 && pos + 2 < len; ++word) {
        for (size_t byte = 0; byte < 4 && pos + 2 < len; ++byte) {
            uint8_t value = (uint8_t)(uid[word] >> (byte * 8));
            buf[pos++] = hex[value >> 4];
            buf[pos++] = hex[value & 0xf];
        }
    }
    if (len != 0) {
        buf[pos] = '\0';
    }
}

void mboot_port_led(uint32_t mask) {
    mboot_led_set(mask);
}


void mboot_port_log_write(const char *s, size_t n) {
    (void)s;
    (void)n;
}

#if defined(MBOOT_ROLE_BOOTLOADER) && MBOOT_ROLE_BOOTLOADER
static bool mboot_usb_clocks_enabled;

int mboot_port_usb_init(void) {
    if (!CLOCK_EnableUsbhs0PhyPllClock(kCLOCK_Usbphy480M, BOARD_XTAL0_CLK_HZ)) {
        return -EIO;
    }
    if (!CLOCK_EnableUsbhs0Clock(kCLOCK_Usb480M, BOARD_XTAL0_CLK_HZ)) {
        CLOCK_DisableUsbhs0PhyPllClock();
        return -EIO;
    }
    mboot_usb_clocks_enabled = true;
    mimxrt_usb_phy_init(0b0111, 0b0110, 0b0110);
    NVIC_ClearPendingIRQ(USB_OTG1_IRQn);
    NVIC_SetPriority(USB_OTG1_IRQn, 2);
    NVIC_EnableIRQ(USB_OTG1_IRQn);
    return 0;
}

void mboot_port_usb_deinit(void) {
    NVIC_DisableIRQ(USB_OTG1_IRQn);
    NVIC_ClearPendingIRQ(USB_OTG1_IRQn);
    if (!mboot_usb_clocks_enabled) {
        return;
    }
    if (tud_inited()) {
        tud_disconnect();
        tusb_deinit(MBOOT_USB_RHPORT);
    }
    // The PHY registers are only accessible while the USB clock runs.
    CLOCK_DisableUsbhs0PhyPllClock();
    #if defined(MIMXRT117x_SERIES)
    CLOCK_DisableClock(kCLOCK_Usb);
    #else
    CLOCK_DisableClock(kCLOCK_UsbOh3);
    #endif
    mboot_usb_clocks_enabled = false;
}

void USB_OTG1_IRQHandler(void) {
    tud_int_handler(MBOOT_USB_RHPORT);
}

void SysTick_Handler(void) {
    ++mboot_ticks;
    mboot_led_tick_1ms();
}
#endif
