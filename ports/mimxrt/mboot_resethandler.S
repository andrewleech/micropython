/* Reset handler for the ROM-booted Mboot image. */
    .syntax unified
    .thumb
    .section .text.Reset_Handler,"ax",%progbits
    .align 2
    .global Reset_Handler
    .type Reset_Handler, %function
    .include "flexram_config.s"

Reset_Handler:
    cpsid   i
    dsb
    isb

    /* Hard-float code is built in; grant CP10/CP11 full access before any C runs. */
    ldr     r0, =0xE000ED88
    ldr     r1, [r0]
    orr     r1, r1, #(0xF << 20)
    str     r1, [r0]
    dsb
    isb
    ldr     r0, =__iomux_gpr17_adr
    ldr     r1, =__iomux_gpr17_value
    str     r1, [r0]
    dsb
    isb
#if defined(MIMXRT117x_SERIES)
    ldr     r0, =__iomux_gpr18_adr
    ldr     r1, =__iomux_gpr18_value
    str     r1, [r0]
    dsb
    isb
#endif
    ldr     r0, =__iomux_gpr16_adr
    ldr     r1, [r0]
    orr     r1, r1, #4
    str     r1, [r0]
    dsb
    isb

    ldr     r0, =_estack
    msr     msp, r0

    /* Relocate vectors into OCRAM before enabling USB or servicing faults. */
    ldr     r0, =__Vectors
    ldr     r1, =__Vectors_RAM
    movs    r2, #0
    ldr     r3, =0x400
1:
    ldr     r4, [r0, r2]
    str     r4, [r1, r2]
    adds    r2, #4
    cmp     r2, r3
    blo     1b
    ldr     r0, =0xE000ED08
    str     r1, [r0]
    dsb
    isb

    /* Copy initialized data and the complete flash-busy call graph to OCRAM. */
    ldr     r0, =_sidata
    ldr     r1, =_sdata
    ldr     r2, =_edata
2:
    cmp     r1, r2
    bhs     3f
    ldr     r3, [r0]
    str     r3, [r1]
    adds    r0, #4
    adds    r1, #4
    b       2b
3:
    ldr     r0, =__ram_functions_load__
    ldr     r1, =__ram_function_start__
    ldr     r2, =__ram_function_end__
4:
    cmp     r1, r2
    bhs     5f
    ldr     r3, [r0]
    str     r3, [r1]
    adds    r0, #4
    adds    r1, #4
    b       4b
5:
    ldr     r1, =_sbss
    ldr     r2, =_ebss
    movs    r3, #0
6:
    cmp     r1, r2
    bhs     7f
    str     r3, [r1]
    adds    r1, #4
    b       6b
7:
    bl      main
8:
    b       8b
    .size Reset_Handler, .-Reset_Handler
