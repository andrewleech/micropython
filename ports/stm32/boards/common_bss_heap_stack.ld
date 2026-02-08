/* This linker script fragment is intended to be included in SECTIONS. */

/* Zeroed-out data section */
.bss :
{
    . = ALIGN(4);
    _sbss = .;
    *(.bss*)
    *(COMMON)
    . = ALIGN(4);
    _ebss = .;
} >RAM

/* NOTE: Zephyr iterable sections are now in .data section */
/* (see common_extratext_data_in_flash.ld) */

/* This is to define the start of the heap, and make sure there is a minimum size */
/* The linker automatically places .noinit sections after .bss */
/* _heap_start is set to the actual start of .heap, after any .noinit sections */
.heap (NOLOAD) :
{
    . = ALIGN(4);
    _heap_start = .;  /* Set _heap_start to actual heap location (after .noinit) */
    . = . + _minimum_heap_size;
    . = ALIGN(4);
} >RAM

/* This checks there is enough RAM for the stack */
.stack :
{
    . = ALIGN(4);
    . = . + _minimum_stack_size;
    . = ALIGN(4);
} >RAM

/* _bl_state symbol is used by MICROPY_HW_ENTER_BOOTLOADER_VIA_RESET, this is
   the end of stack address but is accessed as a different type. */
_bl_state = _estack;
