/*
    GNU linker script for STM32H573xI, application running under the MCUboot bootloader
    (MCUBOOT=1, boards/common_bl.ld places the sections).

    The flash addresses come from the board layout, mcuboot_layout.ld, and the SRAM addresses
    from mcuboot_sram.ld, which the build generates from ports/stm32/mcuboot/mcuboot_sram.ld.S.
    Both must be passed to the linker before this script:
      MCUBOOT_APP_START, MCUBOOT_APP_LEN   the primary slot after the image header
      MCUBOOT_FS_START, MCUBOOT_FS_SIZE    the filesystem area
      mcuboot_req_start, MCUBOOT_REQ_SIZE  the bootloader request region at the end of SRAM
*/

/* Specify the memory areas */
MEMORY
{
    FLASH_APP (rx)  : ORIGIN = MCUBOOT_APP_START, LENGTH = MCUBOOT_APP_LEN
    FLASH_FS (rx)   : ORIGIN = MCUBOOT_FS_START, LENGTH = MCUBOOT_FS_SIZE
    RAM (xrw)       : ORIGIN = MCUBOOT_SRAM_START, LENGTH = mcuboot_req_start - 8K - MCUBOOT_SRAM_START    /* SRAM1+SRAM2+SRAM3 up to the filesystem cache */
    FS_CACHE (xrw)  : ORIGIN = mcuboot_req_start - 8K, LENGTH = 8K
    MCUBOOT_REQ (rw) : ORIGIN = mcuboot_req_start, LENGTH = MCUBOOT_REQ_SIZE
}

/* produce a link error if there is not this amount of RAM for these sections */
_minimum_stack_size = 2K;
_minimum_heap_size = 16K;

/* Define the stack.  The stack is full descending so begins just above last byte
   of RAM.  Note that EABI requires the stack to be 8-byte aligned for a call. */
_estack = ORIGIN(RAM) + LENGTH(RAM) - _estack_reserve;
_sstack = _estack - 16K; /* tunable */

/* RAM extents for the garbage collector */
_ram_start = ORIGIN(RAM);
_ram_end = ORIGIN(RAM) + LENGTH(RAM);
_heap_start = _ebss; /* heap starts just after statically allocated memory */
_heap_end = _sstack;

/* Filesystem cache in RAM, and storage in flash */
_micropy_hw_internal_flash_storage_ram_cache_start = ORIGIN(FS_CACHE);
_micropy_hw_internal_flash_storage_ram_cache_end = ORIGIN(FS_CACHE) + LENGTH(FS_CACHE);
_micropy_hw_internal_flash_storage_start = ORIGIN(FLASH_FS);
_micropy_hw_internal_flash_storage_end = ORIGIN(FLASH_FS) + LENGTH(FLASH_FS);

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
