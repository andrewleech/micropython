/*
 * This file is part of the MicroPython project, http://micropython.org/
 *
 * The MIT License (MIT)
 *
 * Copyright (c) 2018 Damien P. George
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

    .syntax unified
    .cpu cortex-m4
    .thumb

    .section .text.Reset_Handler
    .global Reset_Handler
    .type Reset_Handler, %function

Reset_Handler:
    /* Save the first argument to pass through to stm32_main */
    mov  r4, r0

    /* Load the stack pointer */
    ldr  sp, =_estack

    /* Initialise the data section */
    ldr  r1, =_sidata
    ldr  r2, =_sdata
    ldr  r3, =_edata
    b    .data_copy_entry
.data_copy_loop:
    ldr  r0, [r1], #4 /* Should be 4-aligned to be as fast as possible */
    str  r0, [r2], #4
.data_copy_entry:
    cmp  r2, r3
    bcc  .data_copy_loop

    /* Zero out the BSS section */
    movs r0, #0
    ldr  r1, =_sbss
    ldr  r2, =_ebss
    b    .bss_zero_entry
.bss_zero_loop:
    str  r0, [r1], #4 /* Should be 4-aligned to be as fast as possible */
.bss_zero_entry:
    cmp  r1, r2
    bcc  .bss_zero_loop

    /*
     * Switch to PSP for Zephyr threading (optional, weak linkage)
     *
     * When MICROPY_ZEPHYR_THREADING is enabled, zephyr_psp_init switches
     * execution from MSP to PSP (z_main_stack). This must happen:
     * - AFTER .data copy (zephyr_psp_stack_top is in .data)
     * - AFTER .bss zero (z_main_stack could be in .bss)
     * - BEFORE any C code that builds stack frames we want to keep
     *
     * If zephyr_psp_init is not linked (non-threading builds), the weak
     * symbol resolves to 0 and we skip the call.
     */
    .weak zephyr_psp_init
    ldr  r0, =zephyr_psp_init
    cmp  r0, #0
    beq  .skip_psp_init
    blx  r0
.skip_psp_init:

    /* Jump to the main code */
    mov  r0, r4
    b    stm32_main

    .size Reset_Handler, .-Reset_Handler
