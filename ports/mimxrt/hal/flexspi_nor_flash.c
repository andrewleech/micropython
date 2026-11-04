/*
 * Based largely on examples provided by NXP:
 *
 * Copyright (c) 2016, Freescale Semiconductor, Inc.
 * Copyright 2016-2020 NXP
 * All rights reserved.
 *
 * SPDX-License-Identifier: BSD-3-Clause
 *
 * Port-specific extensions and adaptions:
 *
 * The MIT License (MIT)
 * This file is part of the MicroPython project, http://micropython.org/
 *
 * Copyright (c) 2021 Damien P. George
 * Copyright (c) 2021 Philipp Ebensberger
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
#include <assert.h>
#include "fsl_common.h"
#include "fsl_clock.h"
#include "flexspi_nor_flash.h"
#include "flexspi_flash_config.h"
#if defined(MBOOT_ROLE_BOOTLOADER) && MBOOT_ROLE_BOOTLOADER
#include "mboot_port.h"
#elif defined(MICROPY_HW_MBOOT_APP) && MICROPY_HW_MBOOT_APP
#include "mphalport.h"
#endif


bool flash_busy_status_pol = 0;
bool flash_busy_status_offset = 0;

uint32_t LUT_pageprogram_quad[4] = {
    // 10 Page Program - quad mode
    FLEXSPI_LUT_SEQ(CMD_SDR, FLEXSPI_1PAD, 0x32, RADDR_SDR, FLEXSPI_1PAD, 24),
    FLEXSPI_LUT_SEQ(WRITE_SDR, FLEXSPI_4PAD, 0x04, STOP, FLEXSPI_1PAD, 0),
    FLEXSPI_LUT_SEQ(0, 0, 0, 0, 0, 0),         // Filler
    FLEXSPI_LUT_SEQ(0, 0, 0, 0, 0, 0),         // Filler
};

uint32_t LUT_write_status[4] = {
    // 4 Write status word for Quad mode
    FLEXSPI_LUT_SEQ(CMD_SDR, FLEXSPI_1PAD, MICROPY_HW_FLASH_QE_CMD, WRITE_SDR, FLEXSPI_1PAD, 0x01),
    FLEXSPI_LUT_SEQ(0, 0, 0, 0, 0, 0),         // Filler
    FLEXSPI_LUT_SEQ(0, 0, 0, 0, 0, 0),         // Filler
    FLEXSPI_LUT_SEQ(0, 0, 0, 0, 0, 0),         // Filler
};

#if defined(MIMXRT117x_SERIES)
static uint8_t div_table_mhz[] = {
    17, // Entry 0 is out of range
    17, // 30 -> 31 MHz
    10, // 50 -> 52.8 MHz
    9, // 60 -> 58.7 MHz
    7, // 75 -> 75.4 MHz
    7, // 80 -> 75.4 MHz
    5, // 100 -> 105.6 Mhz
    4, // 133 -> 132 MHz
    3  // 166 -> 176 MHz
};

#else
typedef struct _ps_div_t {
    uint8_t pfd480_div;
    uint8_t podf_div;
} ps_div_t;

static ps_div_t div_table_mhz[] = {
    { 35, 8 }, // Entry 0 is out of range
    { 35, 8 }, // 30 -> 30.85 MHz
    { 29, 6 }, // 50 -> 49.65 MHz
    { 18, 8 }, // 60 -> 60 MHz
    { 23, 5 }, // 75 -> 75.13 MHz
    { 18, 6 }, // 80 -> 80 MHz
    { 17, 5 }, // 100 -> 101 Mhz
    { 13, 5 }, // 133 -> 132.92 MHz
    { 13, 4 }  // 166  -> 166.15 MHz
};
#endif

__attribute__((section(".ram_functions"))) void flexspi_update_lut_clk(uint32_t freq_index) {
    // Create a local copy of the LookupTable. Modify the entry for WRITESTATUSREG
    // Add an entry for PAGEPROGRAM_QUAD.
    uint32_t lookuptable_copy[64];
    memcpy(lookuptable_copy, (const uint32_t *)&qspiflash_config.memConfig.lookupTable, 64 * sizeof(uint32_t));
    // write local WRITESTATUSREG code to index 4
    memcpy(&lookuptable_copy[NOR_CMD_LUT_SEQ_IDX_WRITESTATUSREG * 4],
        LUT_write_status, 4 * sizeof(uint32_t));
    // write local PAGEPROGRAM_QUAD code to index 10
    memcpy(&lookuptable_copy[NOR_CMD_LUT_SEQ_IDX_PAGEPROGRAM_QUAD * 4],
        LUT_pageprogram_quad, 4 * sizeof(uint32_t));
    // Update the LookupTable.
    FLEXSPI_UpdateLUT(BOARD_FLEX_SPI, 0, lookuptable_copy, 64);

    #if defined(MBOOT_ROLE_BOOTLOADER) && MBOOT_ROLE_BOOTLOADER
    // Mboot configures the clock before returning from RAM clock initialisation.
    (void)freq_index;
    #else
    flexspi_update_clk(freq_index);
    #endif
}

__attribute__((section(".ram_functions"))) void flexspi_update_clk(uint32_t freq_index) {
    __DSB();
    __ISB();
    uint32_t primask = __get_PRIMASK();
    bool dcache_enabled = (SCB->CCR & SCB_CCR_DC_Msk) != 0;
    __disable_irq();
    if (dcache_enabled) {
        SCB_CleanInvalidateDCache();
        SCB_DisableDCache();
    }

    #if defined(MIMXRT117x_SERIES)
    volatile uint8_t pll2_div = div_table_mhz[freq_index] - 1;

    while (!FLEXSPI_GetBusIdleStatus(BOARD_FLEX_SPI)) {
    }
    FLEXSPI_Enable(BOARD_FLEX_SPI, false);

    // Disable FlexSPI clock
    CCM->LPCG[kCLOCK_Flexspi1].DIRECT = ((uint32_t)kCLOCK_Off & CCM_LPCG_DIRECT_ON_MASK);
    // Change the PLL divider
    uint32_t root = CCM->CLOCK_ROOT[kCLOCK_Root_Flexspi1].CONTROL & ~CCM_CLOCK_ROOT_CONTROL_DIV_MASK;
    #if defined(MBOOT_ROLE_BOOTLOADER) && MBOOT_ROLE_BOOTLOADER
    root = (root & ~CCM_CLOCK_ROOT_CONTROL_MUX_MASK) |
        CCM_CLOCK_ROOT_CONTROL_MUX(kCLOCK_FLEXSPI1_ClockRoot_MuxSysPll2Out);
    #endif
    CCM->CLOCK_ROOT[kCLOCK_Root_Flexspi1].CONTROL = root | CCM_CLOCK_ROOT_CONTROL_DIV(pll2_div);
    // Re-enable FlexSPI clock
    CCM->LPCG[kCLOCK_Flexspi1].DIRECT = ((uint32_t)kCLOCK_On & CCM_LPCG_DIRECT_ON_MASK);

    #else

    volatile uint8_t pfd480_div = div_table_mhz[freq_index].pfd480_div;
    volatile uint8_t podf_div = div_table_mhz[freq_index].podf_div - 1;

    while (!FLEXSPI_GetBusIdleStatus(BOARD_FLEX_SPI)) {
    }
    FLEXSPI_Enable(BOARD_FLEX_SPI, false);

    // Disable FlexSPI clock
    CCM->CCGR6 &= ~CCM_CCGR6_CG5_MASK;
    #if defined(MBOOT_ROLE_BOOTLOADER) && MBOOT_ROLE_BOOTLOADER
    uint32_t pll = CCM_ANALOG->PLL_USB1;
    const uint32_t pll_ready = CCM_ANALOG_PLL_USB1_POWER_MASK |
        CCM_ANALOG_PLL_USB1_ENABLE_MASK | CCM_ANALOG_PLL_USB1_LOCK_MASK;
    if ((pll & (pll_ready | CCM_ANALOG_PLL_USB1_BYPASS_MASK | CCM_ANALOG_PLL_USB1_DIV_SELECT_MASK)) != pll_ready) {
        const clock_usb_pll_config_t config = { .loopDivider = 0, .src = 0 };
        CLOCK_InitUsb1Pll(&config);
    }
    CLOCK_InitUsb1Pfd(kCLOCK_Pfd0, pfd480_div);
    #else
    CCM_ANALOG->PFD_480 = (CCM_ANALOG->PFD_480 & ~CCM_ANALOG_PFD_480_TOG_PFD0_FRAC_MASK) | CCM_ANALOG_PFD_480_TOG_PFD0_FRAC(pfd480_div);
    #endif
    uint32_t root = CCM->CSCMR1 & ~CCM_CSCMR1_FLEXSPI_PODF_MASK;
    #if defined(MBOOT_ROLE_BOOTLOADER) && MBOOT_ROLE_BOOTLOADER
    root = (root & ~CCM_CSCMR1_FLEXSPI_CLK_SEL_MASK) | CCM_CSCMR1_FLEXSPI_CLK_SEL(3);
    #endif
    CCM->CSCMR1 = root | CCM_CSCMR1_FLEXSPI_PODF(podf_div);
    // Re-enable FlexSPI
    CCM->CCGR6 |= CCM_CCGR6_CG5_MASK;
    #endif

    FLEXSPI_Enable(BOARD_FLEX_SPI, true);
    FLEXSPI_SoftwareReset(BOARD_FLEX_SPI);
    while (!FLEXSPI_GetBusIdleStatus(BOARD_FLEX_SPI)) {
    }

    if (dcache_enabled) {
        SCB_InvalidateDCache();
        SCB_EnableDCache();
    }
    __DSB();
    __ISB();
    if ((primask & 1u) == 0) {
        __enable_irq();
    }
}

static inline __attribute__((always_inline)) void flexspi_nor_poll_watchdog(void) {
    #if defined(MBOOT_ROLE_BOOTLOADER) && MBOOT_ROLE_BOOTLOADER
    mboot_port_wdt_feed();
    #elif defined(MICROPY_HW_MBOOT_APP) && MICROPY_HW_MBOOT_APP
    // Match SysTick's inherited-watchdog service without feeding machine.WDT.
    mimxrt_wdt_feed_aux();
    #endif
}

status_t flexspi_nor_reset(FLEXSPI_Type *base) __attribute__((section(".ram_functions")));
status_t flexspi_nor_reset(FLEXSPI_Type *base) {
    flexspi_nor_timeout_t timeout;
    flexspi_nor_timeout_start(&timeout, 10);
    base->MCR0 |= FLEXSPI_MCR0_SWRESET_MASK;
    while (base->MCR0 & FLEXSPI_MCR0_SWRESET_MASK) {
        if (flexspi_nor_timeout_expired(&timeout)) {
            return kStatus_Timeout;
        }
    }
    return kStatus_Success;
}

static status_t flexspi_nor_wait_flags(FLEXSPI_Type *base, uint32_t mask, bool rx,
    flexspi_nor_timeout_t *timeout) __attribute__((section(".ram_functions")));
static status_t flexspi_nor_wait_flags(FLEXSPI_Type *base, uint32_t mask, bool rx,
    flexspi_nor_timeout_t *timeout) {
    for (;;) {
        uint32_t flags = base->INTR;
        status_t status = FLEXSPI_CheckAndClearError(base, flags);
        if (status != kStatus_Success) {
            return status;
        }
        if (rx ? (base->IPRXFSTS & FLEXSPI_IPRXFSTS_FILL_MASK) != 0 : (flags & mask) != 0) {
            return kStatus_Success;
        }
        if (flexspi_nor_timeout_expired(timeout)) {
            return kStatus_Timeout;
        }
        flexspi_nor_poll_watchdog();
    }
}

// The SDK blocking and nonblocking paths contain unbounded FIFO waits. This
// bounded IP path handles only the NOR commands, status byte and page writes.
static status_t flexspi_nor_transfer(FLEXSPI_Type *base, const flexspi_transfer_t *xfer)
__attribute__((section(".ram_functions")));
static status_t flexspi_nor_transfer(FLEXSPI_Type *base, const flexspi_transfer_t *xfer) {
    flexspi_nor_timeout_t timeout;
    flexspi_nor_timeout_start(&timeout, 100);
    base->FLSHCR2[xfer->port] |= FLEXSPI_FLSHCR2_CLRINSTRPTR_MASK;
    base->INTR = FLEXSPI_INTR_AHBCMDERR_MASK | FLEXSPI_INTR_IPCMDERR_MASK |
        FLEXSPI_INTR_AHBCMDGE_MASK | FLEXSPI_INTR_IPCMDGE_MASK | FLEXSPI_INTR_IPCMDDONE_MASK;
    base->IPCR0 = xfer->deviceAddress;
    base->IPTXFCR |= FLEXSPI_IPTXFCR_CLRIPTXF_MASK;
    base->IPRXFCR |= FLEXSPI_IPRXFCR_CLRIPRXF_MASK;
    uint32_t size = xfer->cmdType == kFLEXSPI_Command ? 0 : xfer->dataSize;
    base->IPCR1 = FLEXSPI_IPCR1_IDATSZ(size) | FLEXSPI_IPCR1_ISEQID(xfer->seqIndex) |
        FLEXSPI_IPCR1_ISEQNUM(xfer->SeqNumber - 1u);
    base->IPCMD |= FLEXSPI_IPCMD_TRG_MASK;

    if (xfer->cmdType == kFLEXSPI_Write) {
        const uint8_t *src = (const uint8_t *)xfer->data;
        uint32_t watermark = 8u * (((base->IPTXFCR & FLEXSPI_IPTXFCR_TXWMRK_MASK) >>
            FLEXSPI_IPTXFCR_TXWMRK_SHIFT) + 1u);
        while (size != 0) {
            status_t status = flexspi_nor_wait_flags(base, FLEXSPI_INTR_IPTXWE_MASK, false, &timeout);
            if (status != kStatus_Success) {
                return status;
            }
            uint32_t count = size < watermark ? size : watermark;
            uint32_t i = 0;
            for (; i < count / 4u; ++i) {
                base->TFDR[i] = *(const uint32_t *)src;
                src += 4;
            }
            if (count & 3u) {
                uint32_t word = 0;
                for (uint32_t j = 0; j < (count & 3u); ++j) {
                    word |= (uint32_t)*src++ << (j * 8u);
                }
                base->TFDR[i] = word;
            }
            size -= count;
            base->INTR = FLEXSPI_INTR_IPTXWE_MASK;
        }
    } else if (xfer->cmdType == kFLEXSPI_Read) {
        status_t status = flexspi_nor_wait_flags(base, 0, true, &timeout);
        if (status != kStatus_Success) {
            return status;
        }
        // The only NOR read through this path is the one-byte status register.
        *xfer->data = base->RFDR[0] & 0xffu;
        base->INTR = FLEXSPI_INTR_IPRXWA_MASK;
    }
    return flexspi_nor_wait_flags(base, FLEXSPI_INTR_IPCMDDONE_MASK, false, &timeout);
}

status_t flexspi_nor_write_enable(FLEXSPI_Type *base, uint32_t baseAddr) __attribute__((section(".ram_functions")));
status_t flexspi_nor_write_enable(FLEXSPI_Type *base, uint32_t baseAddr) {
    flexspi_transfer_t flashXfer;
    status_t status;

    /* Write neable */
    flashXfer.deviceAddress = baseAddr;
    flashXfer.port = kFLEXSPI_PortA1;
    flashXfer.cmdType = kFLEXSPI_Command;
    flashXfer.SeqNumber = 1;
    flashXfer.seqIndex = NOR_CMD_LUT_SEQ_IDX_WRITEENABLE;

    status = flexspi_nor_transfer(base, &flashXfer);
    if (status != kStatus_Success && flexspi_nor_reset(base) != kStatus_Success) {
        flash_fatal_reset();
    }

    return status;
}

status_t flexspi_nor_wait_bus_busy(FLEXSPI_Type *base) __attribute__((section(".ram_functions")));
status_t flexspi_nor_wait_bus_busy(FLEXSPI_Type *base) {
    /* Wait status ready. */
    bool isBusy;
    uint32_t readValue;
    status_t status;
    flexspi_transfer_t flashXfer;
    status_t first_error = kStatus_Success;
    flexspi_nor_timeout_t timeout;
    // Leave margin over the supported NOR block erase times without an endless wait.
    flexspi_nor_timeout_start(&timeout, 10000);

    flashXfer.deviceAddress = 0;
    flashXfer.port = kFLEXSPI_PortA1;
    flashXfer.cmdType = kFLEXSPI_Read;
    flashXfer.SeqNumber = 1;
    flashXfer.seqIndex = NOR_CMD_LUT_SEQ_IDX_READSTATUSREG;
    flashXfer.data = &readValue;
    flashXfer.dataSize = 1;

    for (;;) {
        if (flexspi_nor_timeout_expired(&timeout)) {
            flash_fatal_reset();
        }
        status = flexspi_nor_transfer(base, &flashXfer);
        if (status != kStatus_Success) {
            if (first_error == kStatus_Success) {
                first_error = status;
            }
            if (flexspi_nor_reset(base) != kStatus_Success) {
                flash_fatal_reset();
            }
        } else {
            if (FLASH_BUSY_STATUS_POL) {
                isBusy = !(readValue & (1U << FLASH_BUSY_STATUS_OFFSET));
            } else {
                isBusy = (readValue & (1U << FLASH_BUSY_STATUS_OFFSET)) != 0;
            }
            if (!isBusy) {
                return first_error;
            }
        }
        if (flexspi_nor_timeout_expired(&timeout)) {
            flash_fatal_reset();
        }
        flexspi_nor_poll_watchdog();
    }
}

static status_t flexspi_nor_complete(FLEXSPI_Type *base, status_t command_status)
__attribute__((section(".ram_functions")));
static status_t flexspi_nor_complete(FLEXSPI_Type *base, status_t command_status) {
    // Abort an incomplete IP transaction before issuing the NOR status command.
    // Keep the original command failure even if cleanup succeeds.
    if (command_status != kStatus_Success && flexspi_nor_reset(base) != kStatus_Success) {
        flash_fatal_reset();
    }
    status_t status = flexspi_nor_wait_bus_busy(base);
    status_t reset_status = flexspi_nor_reset(base);
    if (reset_status != kStatus_Success) {
        flash_fatal_reset();
    }
    if (command_status != kStatus_Success) {
        return command_status;
    }
    return status;
}

status_t flexspi_nor_enable_quad_mode(FLEXSPI_Type *base) __attribute__((section(".ram_functions")));
status_t flexspi_nor_enable_quad_mode(FLEXSPI_Type *base) {
    flexspi_transfer_t flashXfer;
    status_t status;
    uint32_t writeValue = MICROPY_HW_FLASH_QE_ARG;

    /* Write enable */
    status = flexspi_nor_write_enable(base, 0);

    if (status != kStatus_Success) {
        return status;
    }

    /* Enable quad mode. */
    flashXfer.deviceAddress = 0;
    flashXfer.port = kFLEXSPI_PortA1;
    flashXfer.cmdType = kFLEXSPI_Write;
    flashXfer.SeqNumber = 1;
    flashXfer.seqIndex = NOR_CMD_LUT_SEQ_IDX_WRITESTATUSREG;
    flashXfer.data = &writeValue;
    flashXfer.dataSize = 1;

    status = flexspi_nor_transfer(base, &flashXfer);
    return flexspi_nor_complete(base, status);
}

status_t flexspi_nor_flash_erase_sector(FLEXSPI_Type *base, uint32_t address) __attribute__((section(".ram_functions")));
status_t flexspi_nor_flash_erase_sector(FLEXSPI_Type *base, uint32_t address) {
    status_t status;
    flexspi_transfer_t flashXfer;

    /* Write enable */
    status = flexspi_nor_write_enable(base, address);

    if (status != kStatus_Success) {
        return status;
    }

    /* Erase sector */
    flashXfer.deviceAddress = address;
    flashXfer.port = kFLEXSPI_PortA1;
    flashXfer.cmdType = kFLEXSPI_Command;
    flashXfer.SeqNumber = 1;
    flashXfer.seqIndex = NOR_CMD_LUT_SEQ_IDX_ERASESECTOR;
    status_t command_status = flexspi_nor_transfer(base, &flashXfer);
    return flexspi_nor_complete(base, command_status);
}

status_t flexspi_nor_flash_erase_block(FLEXSPI_Type *base, uint32_t address) __attribute__((section(".ram_functions")));
status_t flexspi_nor_flash_erase_block(FLEXSPI_Type *base, uint32_t address) {
    status_t status;
    flexspi_transfer_t flashXfer;

    /* Write enable */
    status = flexspi_nor_write_enable(base, address);

    if (status != kStatus_Success) {
        return status;
    }

    /* Erase sector */
    flashXfer.deviceAddress = address;
    flashXfer.port = kFLEXSPI_PortA1;
    flashXfer.cmdType = kFLEXSPI_Command;
    flashXfer.SeqNumber = 1;
    flashXfer.seqIndex = NOR_CMD_LUT_SEQ_IDX_ERASEBLOCK;
    status_t command_status = flexspi_nor_transfer(base, &flashXfer);
    return flexspi_nor_complete(base, command_status);
}

status_t flexspi_nor_flash_page_program(FLEXSPI_Type *base, uint32_t dstAddr, const uint32_t *src, uint32_t size) __attribute__((section(".ram_functions")));
status_t flexspi_nor_flash_page_program(FLEXSPI_Type *base, uint32_t dstAddr, const uint32_t *src, uint32_t size) {
    status_t status;
    flexspi_transfer_t flashXfer;

    /* Write enable */
    status = flexspi_nor_write_enable(base, dstAddr);

    if (status != kStatus_Success) {
        return status;
    }

    /* Prepare page program command */
    flashXfer.deviceAddress = dstAddr;
    flashXfer.port = kFLEXSPI_PortA1;
    flashXfer.cmdType = kFLEXSPI_Write;
    flashXfer.SeqNumber = 1;
    flashXfer.seqIndex = NOR_CMD_LUT_SEQ_IDX_PAGEPROGRAM_QUAD;
    flashXfer.data = (uint32_t *)src;
    flashXfer.dataSize = size;
    status_t command_status = flexspi_nor_transfer(base, &flashXfer);
    return flexspi_nor_complete(base, command_status);
}
