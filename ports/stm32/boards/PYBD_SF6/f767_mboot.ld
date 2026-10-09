/*
    GNU linker script for PYBD with STM32F767, application running under the MCUboot bootloader
    (MBOOT_BACKEND=mcuboot, boards/common_bl.ld places the sections).

    The flash addresses come from the board layout, mboot_layout.ld, and the SRAM addresses
    from mboot_sram.ld, which the build generates from ports/stm32/mboot/mcuboot/mboot_sram.ld.S.
    Both must be passed to the linker before this script:
      MBOOT_APP_START, MBOOT_APP_LEN   the slot after the image header
      mboot_req_start, MBOOT_REQ_SIZE  the bootloader request region at the end of SRAM
*/

/* Specify the memory areas */
MEMORY
{
    FLASH_APP (rx)  : ORIGIN = MBOOT_APP_START, LENGTH = MBOOT_APP_LEN
    FLASH_ROMFS (rx): ORIGIN = 0x90000000, LENGTH = 2048K   /* external QSPI, at least 2MiB */
    RAM (rwx)       : ORIGIN = MBOOT_SRAM_START, LENGTH = mboot_req_start - MBOOT_SRAM_START    /* DTCM=128k, SRAM1=368K, SRAM2=16K without the request region */
    MBOOT_REQ (rw) : ORIGIN = mboot_req_start, LENGTH = MBOOT_REQ_SIZE
}

/* produce a link error if there is not this amount of RAM for these sections */
_minimum_stack_size = 2K;
_minimum_heap_size = 16K;

/* Define the stack.  The stack is full descending so begins just above last byte
   of RAM.  Note that EABI requires the stack to be 8-byte aligned for a call. */
_estack = ORIGIN(RAM) + LENGTH(RAM) - _estack_reserve;
_sstack = _estack - 24K;

/* RAM extents for the garbage collector */
_ram_start = ORIGIN(RAM);
_ram_end = ORIGIN(RAM) + LENGTH(RAM);
_heap_start = _ebss; /* heap starts just after statically allocated memory */
_heap_end = _sstack;

/* ROMFS location */
_micropy_hw_romfs_part0_start = ORIGIN(FLASH_ROMFS);

/* The bootloader request region (see shared/mboot/include/mboot_request.h). It sits outside
   RAM so start-up neither initialises nor clears it; the application reaches it only through
   mboot_port_request_ram(). */
SECTIONS
{
    .mboot_request (NOLOAD) :
    {
        . = . + MBOOT_REQ_SIZE;
    } >MBOOT_REQ
}
