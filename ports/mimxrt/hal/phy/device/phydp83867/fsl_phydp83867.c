/*
 * Copyright 2025 Planet Innovation
 * All rights reserved.
 *
 * Based on NXP RTL8211F driver structure and TI DP83867 specifications.
 * SPDX-License-Identifier: BSD-3-Clause
 */

#include "fsl_phydp83867.h"
#include <stdio.h>

/*******************************************************************************
 * Definitions
 ******************************************************************************/

/*! @brief Defines the PHY DP83867 vendor defined registers. */
#define PHY_PHYSTS_REG          0x11U /*!< The PHY Status register. */

/*! @brief Defines the PHY DP83867 ID number. */
#define PHY_CONTROL_ID1         0x2000U /*!< The PHY ID1 (upper 16 bits). */
#define PHY_CONTROL_ID2         0xA231U /*!< The PHY ID2 (lower 16 bits). */
#define PHY_FULL_ID             0x2000A231U /*!< Full PHY ID. */

/*! @brief Defines the mask flag in PHYSTS register. */
#define PHY_PHYSTS_LINKSTATUS_MASK  0x0400U /*!< The PHY link status mask. */
#define PHY_PHYSTS_LINKSPEED_MASK   0xC000U /*!< The PHY link speed mask. */
#define PHY_PHYSTS_LINKDUPLEX_MASK  0x2000U /*!< The PHY link duplex mask. */
#define PHY_PHYSTS_LINKSPEED_SHIFT  14U     /*!< The link speed shift */

/*! @brief Link speed values from PHYSTS register. */
#define PHY_PHYSTS_LINKSPEED_10M    0U /*!< 10M link speed. */
#define PHY_PHYSTS_LINKSPEED_100M   1U /*!< 100M link speed. */
#define PHY_PHYSTS_LINKSPEED_1000M  2U /*!< 1000M link speed. */

/*! @brief Defines the timeout macro. */
#define PHY_READID_TIMEOUT_COUNT    1000U

/*******************************************************************************
 * Prototypes
 ******************************************************************************/

/*******************************************************************************
 * Variables
 ******************************************************************************/

const phy_operations_t phydp83867_ops = {.phyInit = PHY_DP83867_Init,
                                         .phyWrite = PHY_DP83867_Write,
                                         .phyRead = PHY_DP83867_Read,
                                         .getAutoNegoStatus = PHY_DP83867_GetAutoNegotiationStatus,
                                         .getLinkStatus = PHY_DP83867_GetLinkStatus,
                                         .getLinkSpeedDuplex = PHY_DP83867_GetLinkSpeedDuplex,
                                         .setLinkSpeedDuplex = PHY_DP83867_SetLinkSpeedDuplex,
                                         .enableLoopback = PHY_DP83867_EnableLoopback};

/*******************************************************************************
 * Code
 ******************************************************************************/

status_t PHY_DP83867_Init(phy_handle_t *handle, const phy_config_t *config) {
    uint32_t counter = PHY_READID_TIMEOUT_COUNT;
    status_t result;
    uint32_t regValue = 0U;
    uint32_t id1 = 0U, id2 = 0U;

    printf("DP83867: Init started, PHY addr=0x%02x\n", config->phyAddr);
    printf("DP83867: Config - autoNeg=%d, speed=%d, duplex=%d, enableEEE=%d\n", 
           config->autoNeg, config->speed, config->duplex, config->enableEEE);

    /* Init MDIO interface. */
    MDIO_Init(handle->mdioHandle);

    /* Assign phy address. */
    handle->phyAddr = config->phyAddr;

    /* Check PHY ID. */
    printf("DP83867: Reading PHY ID registers...\n");
    
    /* First, let's read both ID registers to see what we get */
    result = MDIO_Read(handle->mdioHandle, handle->phyAddr, PHY_ID1_REG, &id1);
    if (result != kStatus_Success) {
        printf("DP83867: ERROR - Failed to read PHY_ID1_REG (0x02), result=%d\n", result);
        return result;
    }
    
    result = MDIO_Read(handle->mdioHandle, handle->phyAddr, PHY_ID2_REG, &id2);
    if (result != kStatus_Success) {
        printf("DP83867: ERROR - Failed to read PHY_ID2_REG (0x03), result=%d\n", result);
        return result;
    }
    
    printf("DP83867: PHY ID1=0x%04x (expected 0x%04x), ID2=0x%04x (expected 0x%04x)\n", 
           id1, PHY_CONTROL_ID1, id2, PHY_CONTROL_ID2);
    printf("DP83867: Full PHY ID=0x%08x (expected 0x%08x)\n", 
           (id1 << 16) | id2, PHY_FULL_ID);
    
    /* Try reading some other registers to check communication */
    uint32_t bmcr = 0, bmsr = 0;
    MDIO_Read(handle->mdioHandle, handle->phyAddr, PHY_BASICCONTROL_REG, &bmcr);
    MDIO_Read(handle->mdioHandle, handle->phyAddr, PHY_BASICSTATUS_REG, &bmsr);
    printf("DP83867: BMCR (reg 0)=0x%04x, BMSR (reg 1)=0x%04x\n", bmcr, bmsr);
    
    /* Check if we got the expected PHY ID */
    if (id1 != PHY_CONTROL_ID1) {
        printf("DP83867: ERROR - PHY ID1 mismatch! Trying alternative addresses...\n");
        
        /* Try scanning other addresses */
        for (uint8_t addr = 0; addr < 32; addr++) {
            uint32_t test_id1 = 0, test_id2 = 0;
            if (MDIO_Read(handle->mdioHandle, addr, PHY_ID1_REG, &test_id1) == kStatus_Success &&
                MDIO_Read(handle->mdioHandle, addr, PHY_ID2_REG, &test_id2) == kStatus_Success) {
                if (test_id1 != 0 && test_id1 != 0xFFFF) {
                    printf("  Addr 0x%02x: ID1=0x%04x, ID2=0x%04x\n", addr, test_id1, test_id2);
                }
            }
        }
        return kStatus_Fail;
    }

    /* Reset PHY. */
    printf("DP83867: Resetting PHY...\n");
    result = MDIO_Write(handle->mdioHandle, handle->phyAddr, PHY_BASICCONTROL_REG, PHY_BCTL_RESET_MASK);
    if (result != kStatus_Success) {
        printf("DP83867: ERROR - Failed to write reset command, result=%d\n", result);
        return result;
    }

    /* Wait for reset to complete */
    printf("DP83867: Waiting for reset to complete...\n");
    counter = PHY_READID_TIMEOUT_COUNT;
    do {
        result = MDIO_Read(handle->mdioHandle, handle->phyAddr, PHY_BASICCONTROL_REG, &regValue);
        if (result != kStatus_Success) {
            printf("DP83867: ERROR - Failed to read BMCR during reset wait, result=%d\n", result);
            return result;
        }
        if (counter % 100 == 0) {
            printf("DP83867: Reset wait - BMCR=0x%04x, counter=%d\n", regValue, counter);
        }
        counter--;
    } while ((regValue & PHY_BCTL_RESET_MASK) && (counter != 0U));

    if (counter == 0U) {
        printf("DP83867: ERROR - Reset timeout! BMCR=0x%04x\n", regValue);
        return kStatus_Fail;
    }
    printf("DP83867: Reset complete, BMCR=0x%04x\n", regValue);

    if (config->autoNeg) {
        /* Set the auto-negotiation. */
        printf("DP83867: Configuring auto-negotiation...\n");
        uint32_t anar = PHY_100BASETX_FULLDUPLEX_MASK | PHY_100BASETX_HALFDUPLEX_MASK | 
                       PHY_10BASETX_FULLDUPLEX_MASK | PHY_10BASETX_HALFDUPLEX_MASK | 
                       PHY_IEEE802_3_SELECTOR_MASK;
        printf("DP83867: Writing ANAR=0x%04x\n", anar);
        result = MDIO_Write(handle->mdioHandle, handle->phyAddr, PHY_AUTONEG_ADVERTISE_REG, anar);
        if (result == kStatus_Success) {
            printf("DP83867: Writing 1000BASE-T control=0x%04x\n", PHY_1000BASET_FULLDUPLEX_MASK);
            result = MDIO_Write(handle->mdioHandle, handle->phyAddr, PHY_1000BASET_CONTROL_REG,
                PHY_1000BASET_FULLDUPLEX_MASK);
            if (result == kStatus_Success) {
                result = MDIO_Read(handle->mdioHandle, handle->phyAddr, PHY_BASICCONTROL_REG, &regValue);
                if (result == kStatus_Success) {
                    uint32_t newValue = regValue | PHY_BCTL_AUTONEG_MASK | PHY_BCTL_RESTART_AUTONEG_MASK;
                    printf("DP83867: Enabling autoneg - BMCR 0x%04x -> 0x%04x\n", regValue, newValue);
                    result = MDIO_Write(handle->mdioHandle, handle->phyAddr, PHY_BASICCONTROL_REG, newValue);
                } else {
                    printf("DP83867: ERROR - Failed to read BMCR for autoneg enable\n");
                }
            } else {
                printf("DP83867: ERROR - Failed to write 1000BASE-T control\n");
            }
        } else {
            printf("DP83867: ERROR - Failed to write ANAR\n");
        }
    } else {
        /* Disable isolate mode */
        printf("DP83867: Manual mode - disabling isolate...\n");
        result = MDIO_Read(handle->mdioHandle, handle->phyAddr, PHY_BASICCONTROL_REG, &regValue);
        if (result != kStatus_Success) {
            printf("DP83867: ERROR - Failed to read BMCR for isolate disable\n");
            return result;
        }
        uint32_t newValue = regValue & ~PHY_BCTL_ISOLATE_MASK;
        printf("DP83867: BMCR isolate disable: 0x%04x -> 0x%04x\n", regValue, newValue);
        result = MDIO_Write(handle->mdioHandle, handle->phyAddr, PHY_BASICCONTROL_REG, newValue);
        if (result != kStatus_Success) {
            printf("DP83867: ERROR - Failed to write BMCR for isolate disable\n");
            return result;
        }

        /* Disable the auto-negotiation and set user-defined speed/duplex configuration. */
        printf("DP83867: Setting manual speed/duplex...\n");
        result = PHY_DP83867_SetLinkSpeedDuplex(handle, config->speed, config->duplex);
    }
    
    printf("DP83867: Init %s (result=%d)\n", 
           result == kStatus_Success ? "SUCCESS" : "FAILED", result);
    return result;
}

status_t PHY_DP83867_Write(phy_handle_t *handle, uint32_t phyReg, uint32_t data) {
    status_t result = MDIO_Write(handle->mdioHandle, handle->phyAddr, phyReg, data);
    if (result != kStatus_Success) {
        printf("DP83867: Write failed - addr=0x%02x, reg=0x%02x, data=0x%04x, result=%d\n",
               handle->phyAddr, phyReg, data, result);
    }
    return result;
}

status_t PHY_DP83867_Read(phy_handle_t *handle, uint32_t phyReg, uint32_t *dataPtr) {
    status_t result = MDIO_Read(handle->mdioHandle, handle->phyAddr, phyReg, dataPtr);
    if (result != kStatus_Success) {
        printf("DP83867: Read failed - addr=0x%02x, reg=0x%02x, result=%d\n",
               handle->phyAddr, phyReg, result);
    }
    return result;
}

status_t PHY_DP83867_GetAutoNegotiationStatus(phy_handle_t *handle, bool *status) {
    assert(status);

    status_t result;
    uint32_t regValue;

    *status = false;

    /* Check auto negotiation complete. */
    result = MDIO_Read(handle->mdioHandle, handle->phyAddr, PHY_BASICSTATUS_REG, &regValue);
    if (result == kStatus_Success) {
        if ((regValue & PHY_BSTATUS_AUTONEGCOMP_MASK) != 0U) {
            *status = true;
        }
    }
    return result;
}

status_t PHY_DP83867_GetLinkStatus(phy_handle_t *handle, bool *status) {
    assert(status);

    status_t result;
    uint32_t regValue;

    /* Read the PHY Status register. */
    result = MDIO_Read(handle->mdioHandle, handle->phyAddr, PHY_PHYSTS_REG, &regValue);
    if (result == kStatus_Success) {
        if ((PHY_PHYSTS_LINKSTATUS_MASK & regValue) != 0U) {
            /* Link up. */
            *status = true;
        } else {
            /* Link down. */
            *status = false;
        }
    }
    return result;
}

status_t PHY_DP83867_GetLinkSpeedDuplex(phy_handle_t *handle, phy_speed_t *speed, phy_duplex_t *duplex) {
    assert(!((speed == NULL) && (duplex == NULL)));

    status_t result;
    uint32_t regValue;

    /* Read the status register. */
    result = MDIO_Read(handle->mdioHandle, handle->phyAddr, PHY_PHYSTS_REG, &regValue);
    if (result == kStatus_Success) {
        if (speed != NULL) {
            switch ((regValue & PHY_PHYSTS_LINKSPEED_MASK) >> PHY_PHYSTS_LINKSPEED_SHIFT)
            {
                case PHY_PHYSTS_LINKSPEED_10M:
                    *speed = kPHY_Speed10M;
                    break;
                case PHY_PHYSTS_LINKSPEED_100M:
                    *speed = kPHY_Speed100M;
                    break;
                case PHY_PHYSTS_LINKSPEED_1000M:
                    *speed = kPHY_Speed1000M;
                    break;
                default:
                    *speed = kPHY_Speed10M;
                    break;
            }
        }

        if (duplex != NULL) {
            if ((regValue & PHY_PHYSTS_LINKDUPLEX_MASK) != 0U) {
                *duplex = kPHY_FullDuplex;
            } else {
                *duplex = kPHY_HalfDuplex;
            }
        }
    }
    return result;
}

status_t PHY_DP83867_SetLinkSpeedDuplex(phy_handle_t *handle, phy_speed_t speed, phy_duplex_t duplex) {
    status_t result;
    uint32_t regValue;

    result = MDIO_Read(handle->mdioHandle, handle->phyAddr, PHY_BASICCONTROL_REG, &regValue);
    if (result == kStatus_Success) {
        /* Disable the auto-negotiation and set according to user-defined configuration. */
        regValue &= ~PHY_BCTL_AUTONEG_MASK;
        if (speed == kPHY_Speed1000M) {
            regValue &= ~PHY_BCTL_SPEED0_MASK;
            regValue |= PHY_BCTL_SPEED1_MASK;
        } else if (speed == kPHY_Speed100M) {
            regValue |= PHY_BCTL_SPEED0_MASK;
            regValue &= ~PHY_BCTL_SPEED1_MASK;
        } else {
            regValue &= ~PHY_BCTL_SPEED0_MASK;
            regValue &= ~PHY_BCTL_SPEED1_MASK;
        }
        if (duplex == kPHY_FullDuplex) {
            regValue |= PHY_BCTL_DUPLEX_MASK;
        } else {
            regValue &= ~PHY_BCTL_DUPLEX_MASK;
        }
        result = MDIO_Write(handle->mdioHandle, handle->phyAddr, PHY_BASICCONTROL_REG, regValue);
    }
    return result;
}

status_t PHY_DP83867_EnableLoopback(phy_handle_t *handle, phy_loop_t mode, phy_speed_t speed, bool enable) {
    /* This PHY only supports local loopback. */
    assert(mode == kPHY_LocalLoop);

    status_t result;
    uint32_t regValue;

    /* Set the loop mode. */
    if (enable) {
        if (speed == kPHY_Speed1000M) {
            regValue = PHY_BCTL_SPEED1_MASK | PHY_BCTL_DUPLEX_MASK | PHY_BCTL_LOOP_MASK;
        } else if (speed == kPHY_Speed100M) {
            regValue = PHY_BCTL_SPEED0_MASK | PHY_BCTL_DUPLEX_MASK | PHY_BCTL_LOOP_MASK;
        } else {
            regValue = PHY_BCTL_DUPLEX_MASK | PHY_BCTL_LOOP_MASK;
        }
        result = MDIO_Write(handle->mdioHandle, handle->phyAddr, PHY_BASICCONTROL_REG, regValue);
    } else {
        /* First read the current status in control register. */
        result = MDIO_Read(handle->mdioHandle, handle->phyAddr, PHY_BASICCONTROL_REG, &regValue);
        if (result == kStatus_Success) {
            regValue &= ~PHY_BCTL_LOOP_MASK;
            result = MDIO_Write(handle->mdioHandle, handle->phyAddr, PHY_BASICCONTROL_REG,
                (regValue | PHY_BCTL_RESTART_AUTONEG_MASK));
        }
    }
    return result;
}