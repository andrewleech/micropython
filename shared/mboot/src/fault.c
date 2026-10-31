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

// The fault policy of the bootloader for Cortex-M ports. Every fault, and an NMI that is not a
// flash ECC event, logs one line and resets. Consecutive resets are counted in a backup word
// and reported by mboot_port_fault_resets(). After 3 of them the main flow goes to recovery (DFU)
// instead of repeating the fault. mboot_fault_count_clear() resets the count; the port calls it
// when the bootloader hands over to the application.

#include "mboot_layout.h"
#include "mboot_port.h"

#if defined(__arm__)

#define SCB_SHCSR (*(volatile uint32_t *)0xE000ED24u)
#define SCB_CFSR (*(volatile uint32_t *)0xE000ED28u)
#define SCB_HFSR (*(volatile uint32_t *)0xE000ED2Cu)
#define SCB_BFAR (*(volatile uint32_t *)0xE000ED38u)

// SHCSR.MEMFAULTENA, BUSFAULTENA, USGFAULTENA and, on ARMv8-M, SECUREFAULTENA.
#if defined(__ARM_ARCH_8M_MAIN__)
#define FAULT_ENABLE_BITS ((1u << 16) | (1u << 17) | (1u << 18) | (1u << 19))
#else
#define FAULT_ENABLE_BITS ((1u << 16) | (1u << 17) | (1u << 18))
#endif

// 0x4D46 | the number of consecutive fault resets.
#define FAULTS_KEY 0x4D460000u
#define FAULTS_KEY_MASK 0xFFFF0000u

void mboot_fault_enable(void) {
    SCB_SHCSR |= FAULT_ENABLE_BITS;
}

void mboot_fault_disable(void) {
    SCB_SHCSR &= ~FAULT_ENABLE_BITS;
}

uint32_t mboot_port_fault_resets(void) {
    uint32_t v = mboot_port_bkp_read(MBOOT_BKP_FAULTS);
    return (v & FAULTS_KEY_MASK) == FAULTS_KEY ? v & 0xFFFFu : 0;
}

void mboot_fault_count_clear(void) {
    mboot_port_bkp_write(MBOOT_BKP_FAULTS, 0);
}

static void fault_hex(uint32_t v) {
    char b[8];
    for (int i = 0; i < 8; i++) {
        uint32_t d = (v >> (28 - 4 * i)) & 0xFu;
        b[i] = d < 10 ? '0' + d : 'A' + d - 10;
    }
    mboot_port_log_write(b, 8);
}

static void fault_text(const char *s) {
    size_t n = 0;
    while (s[n] != 0) {
        n++;
    }
    mboot_port_log_write(s, n);
}

static MBOOT_NORETURN void fault_reset(void) {
    uint32_t n = mboot_port_fault_resets() + 1;
    mboot_port_bkp_write(MBOOT_BKP_FAULTS, FAULTS_KEY | (n < 0xFFFFu ? n : 0xFFFFu));
    mboot_port_reset();
}

__attribute__((used, noreturn)) static void fault_report(uint32_t id, uint32_t pc) {
    fault_text("\nFAULT ");
    fault_hex(id);
    fault_text(" pc=");
    fault_hex(pc);
    fault_text(" cfsr=");
    fault_hex(SCB_CFSR);
    fault_text(" hfsr=");
    fault_hex(SCB_HFSR);
    fault_text(" bfar=");
    fault_hex(SCB_BFAR);
    #if MICROPY_HW_MBOOT_DEV0_ECC
    fault_text(" eccd=");
    fault_hex(mboot_ecc_last_info());
    #endif
    fault_text("\n");
    fault_reset();
}

// A failure the port cannot continue from, for example a clock that does not start.
MBOOT_NORETURN void mboot_fault_fatal(const char *msg) {
    fault_text("\nFATAL ");
    fault_text(msg);
    fault_text("\n");
    fault_reset();
}

__attribute__((used)) void mboot_fault_c(const uint32_t *frame, uint32_t id) {
    // The stacked PC is frame[6]. If the stack itself is unusable the value is not trusted and
    // is only logged.
    fault_report(id, frame[6]);
}

#define FAULT_ENTRY(name, id) \
    __attribute__((naked)) void name(void) { \
        __asm volatile ( \
    "tst lr, #4\n" \
    "ite eq\n" \
    "mrseq r0, msp\n" \
    "mrsne r0, psp\n" \
    "movs r1, %0\n" \
    "b mboot_fault_c\n" \
    : : "i" (id)); \
    }

FAULT_ENTRY(HardFault_Handler, 3)
FAULT_ENTRY(MemManage_Handler, 4)
FAULT_ENTRY(BusFault_Handler, 5)
FAULT_ENTRY(UsageFault_Handler, 6)
#if defined(__ARM_ARCH_8M_MAIN__)
FAULT_ENTRY(SecureFault_Handler, 7)
#endif

// A flash double ECC error raises the NMI and the load that caused it still returns data. The
// flash functions detect the error through mboot_port_ecc_events(). Anything else that raises the
// NMI is treated as a fault.
void NMI_Handler(void) {
    #if MICROPY_HW_MBOOT_DEV0_ECC
    if (mboot_ecc_nmi()) {
        return;
    }
    #endif
    fault_report(2, 0);
}

#endif // __arm__
