/*
 * This file is part of the MicroPython project, http://micropython.org/
 *
 * The MIT License (MIT)
 *
 * Copyright (c) 2026 MicroPython contributors
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

// Port-owned interrupt dispatch and trap handler for the Baochip-1x.
//
// Replaces the Dabao SDK's hardware_irq/irq.c (which is excluded from the
// build).  The IRQ-array dispatch protocol matches the SDK's; the
// difference is the exception path: rather than the SDK's "light an LED
// and spin forever", an unexpected RISC-V exception dumps mcause / mepc /
// mtval over the REPL UART so a fault can be located in the disassembly,
// then halts.  The dump uses only the polled uart_write primitive, so it
// is safe to run with the scheduler and USB stack in any state.

#include <stdint.h>

#include "py/mpconfig.h"
#include "hardware/irq.h"
#include "hardware/uart.h"

// IRQ-array base addresses (VexRiscv interrupt controller windows).
// Mirrors the table in the SDK's irq.c.
static const uint32_t irq_array_base[20] = {
    0xE0004000,  /*  0: MDMA, BIO */
    0xE0005000,  /*  1: USB */
    0xE0010000,  /*  2: QFC, MDMA, Mailbox, AO wakeup */
    0xE0011000,  /*  3: Crypto */
    0xE0012000,  /*  4: Crypto dupes */
    0xE0013000,  /*  5: UART */
    0xE0014000,  /*  6: SPI master */
    0xE0015000,  /*  7: I2C */
    0xE0016000,  /*  8: SDIO, I2S, Camera, ADC */
    0xE0017000,  /*  9: SCIF, SPI slave, PWM */
    0xE0006000,  /* 10: IOX, USB, SDDC, BIO */
    0xE0007000,  /* 11: I2S dupes */
    0xE0008000,  /* 12: I2C NACK/error */
    0xE0009000,  /* 13: Bus errors, SCE errors */
    0xE000A000,  /* 14: UART2/3 dupes, TRNG */
    0xE000B000,  /* 15: Security sensor */
    0xE000C000,  /* 16: Camera, I2S, SPIM dupes */
    0xE000D000,  /* 17: I2C1, BIO, QFC, ADC dupes */
    0xE000E000,  /* 18: BIO, I2C2, I2C NACK dupes */
    0xE000F000,  /* 19: Mailbox, BIO, SDIO dupes */
};

#define IRQ_EV_PENDING      0x10
#define IRQ_EV_ENABLE       0x14

// 20 IRQ arrays + slots 20-29 unused + slot 30 for Timer0.
#define IRQ_TABLE_SIZE      31
static irq_handler_t irq_handlers[IRQ_TABLE_SIZE];

static inline uint32_t csr_read_mim(void) {
    uint32_t val;
    __asm__ volatile ("csrr %0, 0xBC0" : "=r" (val));
    return val;
}

static inline void csr_write_mim(uint32_t val) {
    __asm__ volatile ("csrw 0xBC0, %0" : : "r" (val));
}

static inline uint32_t csr_read_mip_vex(void) {
    uint32_t val;
    __asm__ volatile ("csrr %0, 0xFC0" : "=r" (val));
    return val;
}

void irq_init(void) {
    for (int i = 0; i < IRQ_TABLE_SIZE; i++) {
        irq_handlers[i] = (irq_handler_t)0;
    }
    // Disable all IRQ lines in MIM.
    csr_write_mim(0);
    // mie.MEIE (bit 11): machine external interrupts.
    __asm__ volatile ("csrs mie, %0" : : "r" (1 << 11));
    // mstatus.MIE (bit 3): global interrupt enable.
    __asm__ volatile ("csrsi mstatus, 0x8");
}

void irq_set_handler(uint32_t irq_no, irq_handler_t handler) {
    if (irq_no < (uint32_t)IRQ_TABLE_SIZE) {
        irq_handlers[irq_no] = handler;
    }
}

void irq_enable(uint32_t irq_no) {
    if (irq_no < 20) {
        volatile uint32_t *ev_enable =
            (volatile uint32_t *)(irq_array_base[irq_no] + IRQ_EV_ENABLE);
        *ev_enable = 0xFFFF;
    }
    csr_write_mim(csr_read_mim() | (1 << irq_no));
}

void irq_enable_events(uint32_t irq_no, uint32_t event_mask) {
    if (irq_no < 20) {
        volatile uint32_t *ev_enable =
            (volatile uint32_t *)(irq_array_base[irq_no] + IRQ_EV_ENABLE);
        *ev_enable |= event_mask;
    }
    csr_write_mim(csr_read_mim() | (1 << irq_no));
}

void irq_disable(uint32_t irq_no) {
    csr_write_mim(csr_read_mim() & ~(1 << irq_no));
}

// ---- Fault reporting -------------------------------------------------

// Emit a NUL-terminated string over the REPL UART using only the polled
// uart_write path (no scheduler, no stdio, no USB).  Safe in trap context.
static void fault_puts(const char *s) {
    size_t n = 0;
    while (s[n] != '\0') {
        n++;
    }
    uart_write(MICROPY_HW_UART_REPL, (const uint8_t *)s, (uint32_t)n);
}

// Append "<label>0x%08x\n" to the UART.  Self-contained hex formatter so
// the trap path pulls in no printf machinery.
static void fault_put_hex(const char *label, uint32_t val) {
    static const char hexd[] = "0123456789abcdef";
    char buf[11];
    buf[0] = '0';
    buf[1] = 'x';
    for (int i = 0; i < 8; i++) {
        buf[2 + i] = hexd[(val >> ((7 - i) * 4)) & 0xf];
    }
    buf[10] = '\n';
    fault_puts(label);
    uart_write(MICROPY_HW_UART_REPL, (const uint8_t *)buf, sizeof(buf));
}

// Main trap dispatch, called by crt0's _trap_entry on any trap.
void trap_dispatch(void) {
    uint32_t mcause;
    __asm__ volatile ("csrr %0, mcause" : "=r" (mcause));

    if (mcause == 0x8000000B) {
        // Machine external interrupt: walk the VexRiscv MIP and dispatch.
        uint32_t pending = csr_read_mip_vex();

        // Timer0 (bit 30).  No Timer0 handler is registered today; if one
        // is added it must also clear Timer0's EV_PENDING here (the array
        // loop below only covers IRQ arrays 0-19).
        if ((pending & (1 << 30)) && irq_handlers[30]) {
            irq_handlers[30](1);
        }

        // IRQ arrays 0-19.
        for (int i = 0; i < 20; i++) {
            if (pending & (1 << i)) {
                volatile uint32_t *ev_pending =
                    (volatile uint32_t *)(irq_array_base[i] + IRQ_EV_PENDING);
                uint32_t events = *ev_pending;
                if (irq_handlers[i]) {
                    irq_handlers[i](events);
                }
                // W1C the pending events AFTER the handler runs.
                // Mirrors bao1x-hal driver.rs:1556 -- clearing EV_PENDING
                // before the handler has had a chance to clear the
                // upstream condition (e.g. USBSTS.EINT / IMAN.IP for the
                // UDC) leaves an edge-loss window: a new event arriving
                // during handler dispatch finds EV_PENDING already 0 and
                // the irqarray's IP-rising edge already consumed, so
                // when the handler clears IMAN.IP at the end the next
                // event doesn't re-pulse the irqarray and the IRQ is
                // lost.  Clearing EV_PENDING last collapses the window
                // -- any event raised during handling re-sets
                // EV_PENDING after we clear it here, re-firing the IRQ
                // on the next dispatch.
                *ev_pending = events;
            }
        }

        // Re-enable machine external interrupts (mie.MEIE).
        __asm__ volatile ("csrs mie, %0" : : "r" (1 << 11));
        return;
    }

    // Unexpected exception: report and halt.  mepc is the faulting PC --
    // look it up in build-DABAO/firmware.elf to locate the offending
    // instruction; mcause gives the trap cause, mtval the bad address.
    uint32_t mepc, mtval;
    __asm__ volatile ("csrr %0, mepc" : "=r" (mepc));
    __asm__ volatile ("csrr %0, mtval" : "=r" (mtval));

    // crt0's _trap_entry saved all GPRs to a fixed frame based at
    // _stack_top: word 0 = ra (x1), word 1 = interrupted sp (x2),
    // word 8 = s1 (x9), word 31 = mepc.  Read them back to show the
    // interrupted context (stack depth + return address).
    extern uint32_t _stack_top;
    const uint32_t *frame = (const uint32_t *)((uintptr_t)&_stack_top - 34u * 4u);

    fault_puts("\r\n*** baochip TRAP ***\r\n");
    fault_put_hex("  mcause=", mcause);
    fault_put_hex("  mepc  =", mepc);
    fault_put_hex("  mtval =", mtval);
    fault_put_hex("  ra    =", frame[0]);
    fault_put_hex("  sp    =", frame[1]);
    fault_put_hex("  s1    =", frame[8]);
    fault_puts("  halted\r\n");

    for (volatile int spin = 0; spin == 0; spin = 0) {
        // Intentional halt: a fault during USB bring-up should stop here
        // with the dump above, not silently reset into a loop.
    }
}
