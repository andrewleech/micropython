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

    printf("DP83867: Init started, PHY addr=0x%02lx\n", (unsigned long)config->phyAddr);

    /* Init MDIO interface. */
    MDIO_Init(handle->mdioHandle);

    /* Assign phy address. */
    handle->phyAddr = config->phyAddr;

    printf("DP83867: Checking PHY ID at address 0x%02lx\n", (unsigned long)handle->phyAddr);

    /* Check PHY ID. */
    do
    {
        result = MDIO_Read(handle->mdioHandle, handle->phyAddr, PHY_ID1_REG, &regValue);
        if (result != kStatus_Success) {
            printf("DP83867: MDIO read failed, result=0x%08lx\n", (unsigned long)result);
            return result;
        }
        printf("DP83867: PHY ID1 read: 0x%04lx (expected: 0x%04lx), counter=%ld\n", 
               (unsigned long)regValue, (unsigned long)PHY_CONTROL_ID1, (unsigned long)counter);
        counter--;
    } while ((regValue != PHY_CONTROL_ID1) && (counter != 0U));

    if (counter == 0U) {
        printf("DP83867: PHY ID1 timeout, no valid response\n");
        /* Let's also try scanning other addresses to see what's there */
        for (uint32_t addr = 0; addr < 32; addr++) {
            result = MDIO_Read(handle->mdioHandle, addr, PHY_ID1_REG, &regValue);
            if (result == kStatus_Success && regValue != 0xFFFF && regValue != 0x0000) {
                printf("DP83867: Found PHY at addr 0x%02lx, ID1=0x%04lx\n", 
                       (unsigned long)addr, (unsigned long)regValue);
            }
        }
        return kStatus_Fail;
    }

    printf("DP83867: PHY ID1 verified, resetting PHY\n");

    /* Reset PHY. */
    result = MDIO_Write(handle->mdioHandle, handle->phyAddr, PHY_BASICCONTROL_REG, PHY_BCTL_RESET_MASK);
    if (result != kStatus_Success) {
        printf("DP83867: PHY reset write failed, result=0x%08lx\n", (unsigned long)result);
        return result;
    }

    printf("DP83867: Waiting for reset to complete\n");

    /* Wait for reset to complete */
    counter = PHY_READID_TIMEOUT_COUNT;
    do {
        result = MDIO_Read(handle->mdioHandle, handle->phyAddr, PHY_BASICCONTROL_REG, &regValue);
        if (result != kStatus_Success) {
            printf("DP83867: Reset status read failed, result=0x%08lx\n", (unsigned long)result);
            return result;
        }
        if (counter % 100 == 0) {
            printf("DP83867: Reset status: 0x%04lx, counter=%ld\n", 
                   (unsigned long)regValue, (unsigned long)counter);
        }
        counter--;
    } while ((regValue & PHY_BCTL_RESET_MASK) && (counter != 0U));

    if (counter == 0U) {
        printf("DP83867: Reset timeout\n");
        return kStatus_Fail;
    }

    printf("DP83867: Reset complete, configuring PHY\n");

    if (config->autoNeg) {
        printf("DP83867: Configuring auto-negotiation\n");
        /* Set the auto-negotiation. */
        result =
            MDIO_Write(handle->mdioHandle, handle->phyAddr, PHY_AUTONEG_ADVERTISE_REG,
                PHY_100BASETX_FULLDUPLEX_MASK | PHY_100BASETX_HALFDUPLEX_MASK | PHY_10BASETX_FULLDUPLEX_MASK |
                PHY_10BASETX_HALFDUPLEX_MASK | PHY_IEEE802_3_SELECTOR_MASK);
        if (result == kStatus_Success) {
            result = MDIO_Write(handle->mdioHandle, handle->phyAddr, PHY_1000BASET_CONTROL_REG,
                PHY_1000BASET_FULLDUPLEX_MASK);
            if (result == kStatus_Success) {
                result = MDIO_Read(handle->mdioHandle, handle->phyAddr, PHY_BASICCONTROL_REG, &regValue);
                if (result == kStatus_Success) {
                    result = MDIO_Write(handle->mdioHandle, handle->phyAddr, PHY_BASICCONTROL_REG,
                        (regValue | PHY_BCTL_AUTONEG_MASK | PHY_BCTL_RESTART_AUTONEG_MASK));
                }
            }
        }
        if (result != kStatus_Success) {
            printf("DP83867: Auto-negotiation setup failed, result=0x%08lx\n", (unsigned long)result);
        } else {
            printf("DP83867: Auto-negotiation configured successfully\n");
        }
    } else {
        printf("DP83867: Configuring manual speed/duplex\n");
        /* Disable isolate mode */
        result = MDIO_Read(handle->mdioHandle, handle->phyAddr, PHY_BASICCONTROL_REG, &regValue);
        if (result != kStatus_Success) {
            printf("DP83867: Read basic control failed, result=0x%08lx\n", (unsigned long)result);
            return result;
        }
        regValue &= ~PHY_BCTL_ISOLATE_MASK;
        result = MDIO_Write(handle->mdioHandle, handle->phyAddr, PHY_BASICCONTROL_REG, regValue);
        if (result != kStatus_Success) {
            printf("DP83867: Write basic control failed, result=0x%08lx\n", (unsigned long)result);
            return result;
        }

        /* Disable the auto-negotiation and set user-defined speed/duplex configuration. */
        result = PHY_DP83867_SetLinkSpeedDuplex(handle, config->speed, config->duplex);
        if (result != kStatus_Success) {
            printf("DP83867: Manual speed/duplex setup failed, result=0x%08lx\n", (unsigned long)result);
        }
    }
    
    if (result == kStatus_Success) {
        printf("DP83867: Init completed successfully\n");
    } else {
        printf("DP83867: Init failed with result=0x%08lx\n", (unsigned long)result);
    }
    
    return result;
}

status_t PHY_DP83867_Write(phy_handle_t *handle, uint32_t phyReg, uint32_t data) {
    return MDIO_Write(handle->mdioHandle, handle->phyAddr, phyReg, data);
}

status_t PHY_DP83867_Read(phy_handle_t *handle, uint32_t phyReg, uint32_t *dataPtr) {
    return MDIO_Read(handle->mdioHandle, handle->phyAddr, phyReg, dataPtr);
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