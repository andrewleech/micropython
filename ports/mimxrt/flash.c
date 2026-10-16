/*
 * This file is part of the MicroPython project, http://micropython.org/
 *
 * The MIT License (MIT)
 *
 * Copyright (c) 2023 Philipp Ebensberger
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

#include "flash.h"

#if defined(MICROPY_HW_ENABLE_MBOOT) && MICROPY_HW_ENABLE_MBOOT
#include "mboot_port.h"
#if defined(MICROPY_HW_MBOOT_APP) && MICROPY_HW_MBOOT_APP && (defined(ENET_PHY_ADDRESS) || defined(ENET_1_PHY_ADDRESS))
#include "fsl_phy.h"
#include "eth.h"
#endif
static volatile bool flash_command_active;
static volatile bool flash_fault_recovery_active;
#endif

void flash_init(void) {
    // Upload the custom flash configuration
    // And fix the entry for PAGEPROGRAM_QUAD
    // Update the flash CLK
    flexspi_update_lut_clk(MICROPY_HW_FLASH_CLK);

    // Configure FLEXSPI IP FIFO access.
    BOARD_FLEX_SPI->MCR0 &= ~(FLEXSPI_MCR0_ARDFEN_MASK);
    BOARD_FLEX_SPI->MCR0 &= ~(FLEXSPI_MCR0_ATDFEN_MASK);
    BOARD_FLEX_SPI->MCR0 |= FLEXSPI_MCR0_ARDFEN(0);
    BOARD_FLEX_SPI->MCR0 |= FLEXSPI_MCR0_ATDFEN(0);
}

// Keep incoming interrupt and data-cache state across each flash command.
// Stage each page before programming so an XIP source is not read while NOR is busy.
#if defined(MICROPY_HW_ENABLE_MBOOT) && MICROPY_HW_ENABLE_MBOOT
// Never read a clock-gated peripheral: the register access can stall the core.
static inline __attribute__((always_inline)) bool flash_peripheral_clocked(clock_ip_name_t name) {
    #if defined(MIMXRT117x_SERIES)
    return (CCM->LPCG[name].STATUS0 & CCM_LPCG_STATUS0_ON_MASK) != 0;
    #else
    uint32_t index = ((uint32_t)name) >> 8U;
    uint32_t shift = ((uint32_t)name) & 0x1FU;
    return ((((volatile uint32_t *)&CCM->CCGR0)[index] >> shift) & 3U) != 0;
    #endif
}

#if defined(MIMXRT117x_SERIES)
#define FLASH_DMA_CLOCKED() flash_peripheral_clocked(kCLOCK_Edma)
#else
#define FLASH_DMA_CLOCKED() flash_peripheral_clocked(kCLOCK_Dma)
#endif

static bool flash_bus_masters_idle(void) __attribute__((section(".ram_functions")));
static bool flash_bus_masters_idle(void) {
    #if defined(MIMXRT117x_SERIES)
    if (SRC->SCR & SRC_SCR_BT_RELEASE_M4_MASK) {
        return false;
    }
    // The port does not own the QoS controller's DMA rings.
    if (flash_peripheral_clocked(kCLOCK_Enet_Qos)) {
        if (ENET_QOS->MAC_CONFIGURATION & (ENET_QOS_MAC_CONFIGURATION_RE_MASK | ENET_QOS_MAC_CONFIGURATION_TE_MASK)) {
            return false;
        }
        for (size_t i = 0; i < sizeof(ENET_QOS->DMA_CH) / sizeof(ENET_QOS->DMA_CH[0]); ++i) {
            if ((ENET_QOS->DMA_CH[i].DMA_CHX_TX_CTRL & ENET_QOS_DMA_CHX_TX_CTRL_ST_MASK) ||
                (ENET_QOS->DMA_CH[i].DMA_CHX_RX_CTRL & ENET_QOS_DMA_CHX_RX_CTRL_SR_MASK)) {
                return false;
            }
        }
    }
    #endif
    static ENET_Type *const enet[] = ENET_BASE_PTRS;
    static const clock_ip_name_t enet_clocks[] = ENET_CLOCKS;
    for (size_t i = 0; i < sizeof(enet) / sizeof(enet[0]); ++i) {
        if (flash_peripheral_clocked(enet_clocks[i]) && (enet[i]->ECR & ENET_ECR_ETHEREN_MASK)) {
            #if defined(MICROPY_HW_MBOOT_APP) && MICROPY_HW_MBOOT_APP && (defined(ENET_PHY_ADDRESS) || defined(ENET_1_PHY_ADDRESS))
            if (eth_flash_access_safe(enet[i])) {
                continue;
            }
            #endif
            return false;
        }
    }
    static USDHC_Type *const usdhc[] = USDHC_BASE_PTRS;
    static const clock_ip_name_t usdhc_clocks[] = USDHC_CLOCKS;
    for (size_t i = 1; i < sizeof(usdhc) / sizeof(usdhc[0]); ++i) {
        if (flash_peripheral_clocked(usdhc_clocks[i]) &&
            (usdhc[i]->PRES_STATE & (USDHC_PRES_STATE_DLA_MASK | USDHC_PRES_STATE_WTA_MASK | USDHC_PRES_STATE_RTA_MASK))) {
            return false;
        }
    }
    return true;
}
#endif

static bool flash_command_begin(uint32_t *state) __attribute__((section(".ram_functions")));
static bool flash_command_begin(uint32_t *state) {
    uint32_t saved_primask = __get_PRIMASK();
    __disable_irq();
    #if defined(MICROPY_HW_ENABLE_MBOOT) && MICROPY_HW_ENABLE_MBOOT
    *state = saved_primask;
    if (FLASH_DMA_CLOCKED()) {
        if (!(DMA0->CR & DMA_CR_HALT_MASK)) {
            DMA0->CR |= DMA_CR_HALT_MASK;
            *state |= 4u;
        }
        __DSB();
        // HALT blocks new requests and links, but lets the active minor loop finish.
        flexspi_nor_timeout_t timeout;
        flexspi_nor_timeout_start(&timeout, 10);
        while (DMA0->CR & DMA_CR_ACTIVE_MASK) {
            if (flexspi_nor_timeout_expired(&timeout)) {
                if (*state & 4u) {
                    DMA0->CR &= ~DMA_CR_HALT_MASK;
                }
                __DSB();
                __set_PRIMASK(saved_primask);
                return false;
            }
        }
    }
    if (!flash_bus_masters_idle()) {
        if (*state & 4u) {
            DMA0->CR &= ~DMA_CR_HALT_MASK;
            __DSB();
        }
        if ((saved_primask & 1u) == 0) {
            __enable_irq();
        }
        return false;
    }
    #else
    *state = saved_primask;
    #endif
    bool dcache_enabled = (SCB->CCR & SCB_CCR_DC_Msk) != 0;
    if (dcache_enabled) {
        SCB_CleanInvalidateDCache();
        SCB_DisableDCache();
        *state |= 2u;
    }
    #if defined(MICROPY_HW_ENABLE_MBOOT) && MICROPY_HW_ENABLE_MBOOT
    flash_command_active = true;
    #endif
    __DSB();
    __ISB();
    return true;
}

void flash_fatal_reset(void) __attribute__((noreturn, section(".ram_functions")));
void flash_fatal_reset(void) {
    __disable_irq();
    #if defined(MICROPY_HW_ENABLE_MBOOT) && MICROPY_HW_ENABLE_MBOOT
    // The terminal reset must not start a second NOR recovery deadline.
    flash_fault_recovery_active = true;
    mboot_port_reset();
    #else
    NVIC_SystemReset();
    for (;;) {
    }
    #endif
}

static void flash_command_end(uint32_t state) __attribute__((section(".ram_functions")));
static void flash_command_end(uint32_t state) {
    // Returning to XIP requires a usable controller as well as an idle NOR.
    #if defined(MICROPY_INCLUDED_MIMXRT_HAL_FLEXSPI_NOR_FLASH_H)
    if (flexspi_nor_reset(BOARD_FLEX_SPI) != kStatus_Success) {
        flash_fatal_reset();
    }
    #else
    FLEXSPI_SoftwareReset(BOARD_FLEX_SPI);
    #endif
    SCB_InvalidateDCache();
    SCB_InvalidateICache();
    if (state & 2u) {
        SCB_EnableDCache();
    }
    #if defined(MICROPY_HW_ENABLE_MBOOT) && MICROPY_HW_ENABLE_MBOOT
    if (state & 4u) {
        DMA0->CR &= ~DMA_CR_HALT_MASK;
    }
    flash_command_active = false;
    #endif
    __DSB();
    __ISB();
    if ((state & 1u) == 0) {
        __enable_irq();
    }
}

#if defined(MICROPY_HW_ENABLE_MBOOT) && MICROPY_HW_ENABLE_MBOOT
void flash_wait_for_idle(void) __attribute__((section(".ram_functions")));
void flash_wait_for_idle(void) {
    // A nested fault must still reach the hardware reset, not retry recovery.
    if (flash_command_active && !flash_fault_recovery_active) {
        flash_fault_recovery_active = true;
        if (flexspi_nor_reset(BOARD_FLEX_SPI) != kStatus_Success) {
            flash_fatal_reset();
        }
        (void)flexspi_nor_wait_bus_busy(BOARD_FLEX_SPI);
        if (flexspi_nor_reset(BOARD_FLEX_SPI) != kStatus_Success) {
            flash_fatal_reset();
        }
        flash_command_active = false;
    }
}
#endif

// flash_erase_block(erase_addr)
// erases the block starting at addr. Block size according to the flash properties.
__attribute__((section(".ram_functions"))) status_t flash_erase_block(uint32_t erase_addr) {
    uint32_t state;
    if (!flash_command_begin(&state)) {
        return kStatus_Busy;
    }
    status_t status = flexspi_nor_flash_erase_block(BOARD_FLEX_SPI, erase_addr);
    flash_command_end(state);
    return status;
}

// flash_erase_sector(erase_addr_bytes)
// erases the sector starting at addr. Sector size according to the flash properties.
__attribute__((section(".ram_functions"))) status_t flash_erase_sector(uint32_t erase_addr) {
    uint32_t state;
    if (!flash_command_begin(&state)) {
        return kStatus_Busy;
    }
    status_t status = flexspi_nor_flash_erase_sector(BOARD_FLEX_SPI, erase_addr);
    flash_command_end(state);
    return status;
}

// flash_write_block(flash_dest_addr_bytes, data_source, length_bytes)
// writes length_byte data to the destination address
// the vfs driver takes care for erasing the sector if required
__attribute__((section(".ram_functions"))) status_t flash_write_block(uint32_t dest_addr, const uint8_t *src, uint32_t length) {
    uint8_t page_data[256] __attribute__((aligned(32)));
    status_t status = kStatus_Success;

    while (length != 0) {
        uint32_t page_remaining = PAGE_SIZE_BYTES - (dest_addr % PAGE_SIZE_BYTES);
        uint32_t write_length = length < page_remaining ? length : page_remaining;
        if (write_length > sizeof(page_data)) {
            write_length = sizeof(page_data);
        }
        memcpy(page_data, src, write_length);

        uint32_t state;
        if (!flash_command_begin(&state)) {
            status = kStatus_Busy;
            break;
        }
        status = flexspi_nor_flash_page_program(BOARD_FLEX_SPI, dest_addr, (uint32_t *)page_data, write_length);
        flash_command_end(state);
        if (status != kStatus_Success) {
            break;
        }
        length -= write_length;
        src += write_length;
        dest_addr += write_length;
    }
    return status;
}

// flash_read_block(flash_src_addr_bytes, data_dest, length_bytes)
// read length_byte data to the source address
// It is just a shim to provide the same structure for read_block and write_block.
__attribute__((section(".ram_functions"))) void flash_read_block(uint32_t src_addr, uint8_t *dest, uint32_t length) {
    memcpy(dest, (const uint8_t *)(BOARD_FLEX_SPI_ADDR_BASE + src_addr), length);
}
