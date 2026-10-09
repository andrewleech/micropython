/*
 * Linker script of the STM32H5 MCUboot bootloader.
 *
 * The flash addresses come from the board layout, mboot_layout.ld, and the SRAM addresses from
 * mboot_sram.ld (generated from mboot_sram.ld.S). Both must be passed to the linker before
 * this script. The bootloader owns the boot area from MBOOT_BOOT_START; the last 64 bytes hold
 * the info block (section .mboot_info). Its RAM is a window at the start of SRAM. The RAM above
 * the window, up to the request region, is unused; an application may point the status word of
 * a request at an address in it.
 */

MEMORY
{
    FLASH_BL (rx)   : ORIGIN = MBOOT_BOOT_START, LENGTH = MBOOT_BOOT_SIZE - 64
    FLASH_INFO (r)  : ORIGIN = MBOOT_BOOT_START + MBOOT_BOOT_SIZE - 64, LENGTH = 64
    RAM (xrw)       : ORIGIN = MBOOT_SRAM_START, LENGTH = 64K
}

mboot_status_start = ORIGIN(RAM) + LENGTH(RAM);
mboot_status_end = mboot_req_start;
ASSERT(mboot_status_start <= mboot_status_end, "the RAM window of the bootloader reaches the request region")

/* The stack is full descending and begins at the top of the RAM window. */
_minimum_stack_size = 8K;
_estack = ORIGIN(RAM) + LENGTH(RAM);

ENTRY(Reset_Handler)

SECTIONS
{
    .isr_vector :
    {
        . = ALIGN(1024);
        KEEP(*(.isr_vector))
        . = ALIGN(4);
    } >FLASH_BL

    .text :
    {
        . = ALIGN(4);
        *(.text*)
        *(.rodata*)
        . = ALIGN(4);
        _etext = .;
    } >FLASH_BL

    /* Used by the start-up code to initialise .data. */
    _sidata = LOADADDR(.data);

    /* Includes the flash erase and program functions (.ramfunc), which run from RAM. */
    .data :
    {
        . = ALIGN(4);
        _sdata = .;
        *(.data*)
        *(.ramfunc*)
        . = ALIGN(4);
        _edata = .;
    } >RAM AT> FLASH_BL

    .bss :
    {
        . = ALIGN(4);
        _sbss = .;
        *(.bss*)
        *(COMMON)
        . = ALIGN(4);
        _ebss = .;
    } >RAM

    /* Makes the link fail when there is not enough RAM left for the stack. */
    .stack (NOLOAD) :
    {
        . = ALIGN(8);
        . = . + _minimum_stack_size;
    } >RAM

    .mboot_info :
    {
        KEEP(*(.mboot_info))
    } >FLASH_INFO

    .ARM.attributes 0 : { *(.ARM.attributes) }

    /DISCARD/ :
    {
        *(.ARM.exidx*)
        *(.ARM.extab*)
    }
}

ASSERT(SIZEOF(.mboot_info) <= 64, "info block larger than 64 bytes")
