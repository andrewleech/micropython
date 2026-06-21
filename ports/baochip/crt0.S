/*
 * crt0.S - C runtime startup for the Baochip-1x MicroPython port.
 *
 * Derived from the Dabao SDK's runtime/crt0.S (Apache-2.0, Copyright
 * 2026 Armstrong Subero).  The port owns this copy so the trap entry can
 * use a dedicated trap stack (_trap_stack_top) instead of carving scratch
 * space out of the top of the main C stack.  Sharing the main stack let a
 * deep interrupt handler (the USB DCD event-ring drain) overflow the small
 * scratch reserve and corrupt the main thread's frames; a separate trap
 * stack removes that hazard.
 *
 * The trap handler saves all 31 GPRs plus mepc/mstatus/orig-sp to the trap
 * stack, calls the C dispatcher (trap_dispatch), then restores and mret's.
 * Traps do not nest (mstatus.MIE stays 0 until mret), so a single trap
 * stack is sufficient.
 */

    .section .text.start
    .global _start
    .type _start, @function

_start:
    /* Set global pointer (for linker relaxation). */
    .option push
    .option norelax
    la      gp, __global_pointer$
    .option pop

    /* Main C stack starts at _stack_top (which the linker places just
     * below the dedicated trap stack) and grows down. */
    la      sp, _stack_top

    /* Install trap handler. */
    la      t0, _trap_entry
    csrw    mtvec, t0

    /* Zero BSS section. */
    la      t0, _bss_start
    la      t1, _bss_end
1:
    beq     t0, t1, 2f
    sw      zero, 0(t0)
    addi    t0, t0, 4
    j       1b
2:

    /* Copy .data from RRAM (LMA) to SRAM (VMA). */
    la      t0, _data_lma
    la      t1, _data_start
    la      t2, _data_end
3:
    beq     t1, t2, 4f
    lw      t3, 0(t0)
    sw      t3, 0(t1)
    addi    t0, t0, 4
    addi    t1, t1, 4
    j       3b
4:

    /* Call main. */
    call    main

    /* If main returns, halt. */
_hang:
    wfi
    j       _hang


/* ================================================================== */
/*                        Trap Entry Point                            */
/* ================================================================== */
/*
 * On any interrupt or exception the CPU jumps here.  Save ALL 31 GPRs
 * (x1-x31) plus mepc / mstatus / the interrupted sp to the dedicated
 * trap stack, call the C dispatcher, then restore everything and mret.
 *
 * mscratch holds the interrupted sp across the save.  The frame and the
 * C dispatcher both live on the dedicated trap stack (_trap_stack_top),
 * never on the main stack.
 */

    .align 4
    .global _trap_entry
    .type _trap_entry, @function

_trap_entry:
    /* Save the interrupted sp, switch to the dedicated trap stack. */
    csrw    mscratch, sp
    la      sp, _trap_stack_top

    /* Allocate 34 words: x1-x31 (31) + mepc (1) + mstatus (1) + orig_sp (1). */
    addi    sp, sp, -(34*4)

    /* Save all general-purpose registers except sp (x2). */
    sw      x1,   0*4(sp)      /* ra */
    /* x2 (sp) saved later from mscratch */
    sw      x3,   2*4(sp)      /* gp */
    sw      x4,   3*4(sp)      /* tp */
    sw      x5,   4*4(sp)      /* t0 */
    sw      x6,   5*4(sp)      /* t1 */
    sw      x7,   6*4(sp)      /* t2 */
    sw      x8,   7*4(sp)      /* s0/fp */
    sw      x9,   8*4(sp)      /* s1 */
    sw      x10,  9*4(sp)      /* a0 */
    sw      x11, 10*4(sp)      /* a1 */
    sw      x12, 11*4(sp)      /* a2 */
    sw      x13, 12*4(sp)      /* a3 */
    sw      x14, 13*4(sp)      /* a4 */
    sw      x15, 14*4(sp)      /* a5 */
    sw      x16, 15*4(sp)      /* a6 */
    sw      x17, 16*4(sp)      /* a7 */
    sw      x18, 17*4(sp)      /* s2 */
    sw      x19, 18*4(sp)      /* s3 */
    sw      x20, 19*4(sp)      /* s4 */
    sw      x21, 20*4(sp)      /* s5 */
    sw      x22, 21*4(sp)      /* s6 */
    sw      x23, 22*4(sp)      /* s7 */
    sw      x24, 23*4(sp)      /* s8 */
    sw      x25, 24*4(sp)      /* s9 */
    sw      x26, 25*4(sp)      /* s10 */
    sw      x27, 26*4(sp)      /* s11 */
    sw      x28, 27*4(sp)      /* t3 */
    sw      x29, 28*4(sp)      /* t4 */
    sw      x30, 29*4(sp)      /* t5 */
    sw      x31, 30*4(sp)      /* t6 */

    /* Save mepc. */
    csrr    t0, mepc
    sw      t0, 31*4(sp)

    /* Save mstatus (to restore MIE state on exit). */
    csrr    t0, mstatus
    sw      t0, 32*4(sp)

    /* Save the interrupted sp from mscratch. */
    csrr    t0, mscratch
    sw      t0,  1*4(sp)       /* slot 1 = original sp */

    /* Reserve C-dispatcher stack below the register save frame, still
     * within the dedicated trap stack. */
    addi    sp, sp, -256

    /* Call the C dispatcher. */
    call    trap_dispatch

    /* Restore sp to the register save frame. */
    la      sp, _trap_stack_top
    addi    sp, sp, -(34*4)

    /* Restore mepc. */
    lw      t0, 31*4(sp)
    csrw    mepc, t0

    /* Restore mstatus. */
    lw      t0, 32*4(sp)
    csrw    mstatus, t0

    /* Restore all general-purpose registers. */
    lw      x1,   0*4(sp)
    /* x2 (sp) restored last */
    lw      x3,   2*4(sp)
    lw      x4,   3*4(sp)
    lw      x5,   4*4(sp)
    lw      x6,   5*4(sp)
    lw      x7,   6*4(sp)
    lw      x8,   7*4(sp)
    lw      x9,   8*4(sp)
    lw      x10,  9*4(sp)
    lw      x11, 10*4(sp)
    lw      x12, 11*4(sp)
    lw      x13, 12*4(sp)
    lw      x14, 13*4(sp)
    lw      x15, 14*4(sp)
    lw      x16, 15*4(sp)
    lw      x17, 16*4(sp)
    lw      x18, 17*4(sp)
    lw      x19, 18*4(sp)
    lw      x20, 19*4(sp)
    lw      x21, 20*4(sp)
    lw      x22, 21*4(sp)
    lw      x23, 22*4(sp)
    lw      x24, 23*4(sp)
    lw      x25, 24*4(sp)
    lw      x26, 25*4(sp)
    lw      x27, 26*4(sp)
    lw      x28, 27*4(sp)
    lw      x29, 28*4(sp)
    lw      x30, 29*4(sp)
    lw      x31, 30*4(sp)

    /* Restore the interrupted sp LAST. */
    lw      x2,   1*4(sp)

    /* Return from trap. */
    mret
