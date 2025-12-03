/*
 * This file is part of the MicroPython project, http://micropython.org/
 *
 * The MIT License (MIT)
 *
 * Copyright (c) 2025 MicroPython contributors
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

/*
 * PSP Stack Switch for Zephyr Threading on Cortex-M
 *
 * Zephyr's threading model expects the main thread to run on PSP (Process
 * Stack Pointer) while MSP (Main Stack Pointer) is reserved for exception
 * handlers. When using CMSIS startup (which stays on MSP), we need to
 * explicitly switch to PSP before Zephyr threading is initialized.
 *
 * This must be called very early in initialization, before any C function
 * has local variables on the stack that need to survive the switch.
 *
 * Based on the PSP switch sequence from Zephyr's reset.S (lines 213-226).
 */

    .syntax unified
    .thumb

/*
 * zephyr_psp_init - Switch thread mode from MSP to PSP
 *
 * Called from Reset_Handler after data/bss initialization but before
 * jumping to stm32_main. This ensures all C code runs on PSP while MSP
 * is reserved for exception handlers.
 *
 * This function CAN return to caller - after the switch, execution
 * continues on PSP and the caller's return address in LR is still valid.
 */
    .section .text.zephyr_psp_init
    .global zephyr_psp_init
    .type zephyr_psp_init, %function

zephyr_psp_init:
    /* Load PSP stack top from C-computed constant */
    ldr r0, =zephyr_psp_stack_top
    ldr r0, [r0]            /* Dereference: r0 = *zephyr_psp_stack_top */

    /* Set PSP to stack top */
    msr PSP, r0

    /* Switch CONTROL.SPSEL to 1 (use PSP in thread mode) */
    mrs r0, CONTROL
    orrs r0, r0, #2         /* Set SPSEL bit (bit 1) */
    msr CONTROL, r0

    /*
     * ISB is required after modifying CONTROL to ensure the stack
     * pointer change takes effect before any subsequent instructions.
     */
    isb

    /* Return to caller - now running on PSP */
    bx lr

    .size zephyr_psp_init, .-zephyr_psp_init


/*
 * zephyr_switch_to_psp_and_call - Switch to PSP and call function (no return)
 *
 * Alternative entry point that doesn't return. Kept for flexibility.
 */
    .section .text.zephyr_switch_to_psp_and_call
    .global zephyr_switch_to_psp_and_call
    .type zephyr_switch_to_psp_and_call, %function

/*
 * void zephyr_switch_to_psp_and_call(void *psp_top, void (*func)(void))
 *
 * Switch from MSP to PSP and call the specified function.
 * This function NEVER RETURNS to the caller.
 *
 * Parameters:
 *   r0 - Top of PSP stack (highest address, stack grows down)
 *   r1 - Function to call after the switch (must be NORETURN)
 *
 * After this call:
 *   - PSP = psp_top
 *   - CONTROL.SPSEL = 1 (thread mode uses PSP)
 *   - MSP unchanged (used for exception handlers)
 *   - func() is called and never returns
 *
 * IMPORTANT: This function does not return because the caller's stack
 * frame is on MSP, but after the switch we're using PSP. The caller's
 * stack frame is abandoned (safe because z_cstart never returns).
 */
zephyr_switch_to_psp_and_call:
    /* Save the function pointer in a callee-saved register */
    mov r4, r1

    /* Set PSP to the provided stack top */
    msr PSP, r0

    /* Switch CONTROL.SPSEL to 1 (use PSP in thread mode) */
    mrs r0, CONTROL
    orrs r0, r0, #2         /* Set SPSEL bit (bit 1) */
    msr CONTROL, r0

    /*
     * ISB is required after modifying CONTROL to ensure the stack
     * pointer change takes effect before any subsequent instructions.
     */
    isb

    /* Call the function - it must never return */
    bx r4

    .size zephyr_switch_to_psp_and_call, .-zephyr_switch_to_psp_and_call
