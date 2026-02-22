/*
 * soc.h wrapper for MicroPython
 * SoC-specific definitions needed by Zephyr controller code
 */

#ifndef SOC_H_
#define SOC_H_

// Include nRF MDK when building for nRF SoCs.
// Provides NRF_FICR, NRF_RNG, etc. needed by controller HAL and hci_vendor.c.
#if defined(NRF51) || defined(NRF52) || defined(NRF52832_XXAA) || \
    defined(NRF52840_XXAA) || defined(NRF52833_XXAA)
#include "nrf.h"
#endif

#endif /* SOC_H_ */
