/*
    GNU linker script for PYBD with STM32F767, application running under the MCUboot bootloader
    (MCUBOOT=1, boards/common_bl.ld places the sections).

    The flash addresses come from the board layout, mcuboot_layout.ld, and the SRAM addresses
    from mcuboot_sram.ld, which the build generates from ports/stm32/mcuboot/mcuboot_sram.ld.S.
    Both must be passed to the linker before this script:
      MCUBOOT_APP_START, MCUBOOT_APP_LEN   the slot after the image header
      mcuboot_req_start, MCUBOOT_REQ_SIZE  the bootloader request region at the end of SRAM
*/

/* Specify the memory areas */
MEMORY
{
    FLASH_APP (rx)  : ORIGIN = MCUBOOT_APP_START, LENGTH = MCUBOOT_APP_LEN
    FLASH_ROMFS (rx): ORIGIN = 0x90000000, LENGTH = 2048K   /* external QSPI, at least 2MiB */
    RAM (rwx)       : ORIGIN = MCUBOOT_SRAM_START, LENGTH = mcuboot_req_start - MCUBOOT_SRAM_START    /* DTCM=128k, SRAM1=368K, SRAM2=16K without the request region */
    MCUBOOT_REQ (rw) : ORIGIN = mcuboot_req_start, LENGTH = MCUBOOT_REQ_SIZE
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

/* The bootloader request region (see shared/mcuboot/include/mcuboot_request.h). It sits outside
   RAM so start-up neither initialises nor clears it; the application reaches it only through
   mcuboot_port_request_ram(). */
SECTIONS
{
    .mcuboot_request (NOLOAD) :
    {
        . = . + MCUBOOT_REQ_SIZE;
    } >MCUBOOT_REQ
}
