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

#include "mimxrt_reset_cause.h"

#include "fsl_wdog.h"
#ifdef MIMXRT117x_SERIES
#include "fsl_soc_src.h"
#else
#include "fsl_src.h"
#endif

mimxrt_reset_cause_t mimxrt_reset_cause_classify(uint32_t src_flags, uint32_t wdog_flags) {
    #ifdef MIMXRT117x_SERIES
    const uint32_t software_reset_flags =
        kSRC_M7CoreM7RequestResetFlag | kSRC_M7CoreM7LockUpResetFlag | kSRC_M7CoreJtagSWResetFlag;
    // The WDOG1 power-on flag stays set across the WDOG1 software reset that sets its software
    // flag, so that reset is told apart by the core watchdog reset flag of the SRC.
    bool wdog_software = (wdog_flags & kWDOG_SoftwareResetFlag) && (src_flags & kSRC_M7CoreWdogResetFlag);
    if ((wdog_flags & kWDOG_PowerOnResetFlag) && !(src_flags & software_reset_flags) && !wdog_software) {
        return MIMXRT_RESET_CAUSE_POR;
    }
    if (wdog_software) {
        return MIMXRT_RESET_CAUSE_SOFTWARE;
    }
    if (src_flags & (kSRC_M7CoreWdogResetFlag | kSRC_M7CoreWdog3ResetFlag | kSRC_M7CoreWdog4ResetFlag)) {
        return MIMXRT_RESET_CAUSE_WATCHDOG;
    }
    if (src_flags & kSRC_M7CoreIppResetFlag) {
        return MIMXRT_RESET_CAUSE_PIN;
    }
    if (src_flags & software_reset_flags) {
        return MIMXRT_RESET_CAUSE_SOFTWARE;
    }
    if (src_flags & kSRC_M7CoreOverVoltageResetFlag) {
        return MIMXRT_RESET_CAUSE_OVERVOLTAGE;
    }
    #else
    if ((wdog_flags & kWDOG_PowerOnResetFlag) && !(src_flags & SRC_SRSR_LOCKUP_SYSRESETREQ_MASK)) {
        return MIMXRT_RESET_CAUSE_POR;
    }
    if (src_flags & SRC_SRSR_WDOG_RST_B_MASK) {
        return MIMXRT_RESET_CAUSE_WATCHDOG;
    }
    if (src_flags & SRC_SRSR_IPP_RESET_B_MASK) {
        return MIMXRT_RESET_CAUSE_PIN;
    }
    if (src_flags & SRC_SRSR_LOCKUP_SYSRESETREQ_MASK) {
        return MIMXRT_RESET_CAUSE_SOFTWARE;
    }
    #endif
    return MIMXRT_RESET_CAUSE_OTHER;
}
