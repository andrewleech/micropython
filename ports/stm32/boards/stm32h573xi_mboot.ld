/*
    GNU linker script for STM32H573xI, application running under the MCUboot bootloader
    (MBOOT_BACKEND=mcuboot, boards/common_bl.ld places the sections).

    The flash addresses come from the board layout, mboot_layout.ld, and the SRAM addresses
    from mboot_sram.ld, which the build generates from ports/stm32/mboot/mcuboot/mboot_sram.ld.S.
    Both must be passed to the linker before this script:
      MBOOT_APP_START, MBOOT_APP_LEN   the primary slot after the image header
      MBOOT_FS_START, MBOOT_FS_SIZE    the filesystem area
      mboot_req_start, MBOOT_REQ_SIZE  the bootloader request region at the end of SRAM
*/

/* Specify the memory areas */
MEMORY
{
    FLASH_APP (rx)  : ORIGIN = MBOOT_APP_START, LENGTH = MBOOT_APP_LEN
    FLASH_FS (rx)   : ORIGIN = MBOOT_FS_START, LENGTH = MBOOT_FS_SIZE
    RAM (xrw)       : ORIGIN = MBOOT_SRAM_START, LENGTH = mboot_req_start - 8K - MBOOT_SRAM_START    /* SRAM1+SRAM2+SRAM3 up to the filesystem cache */
    FS_CACHE (xrw)  : ORIGIN = mboot_req_start - 8K, LENGTH = 8K
    MBOOT_REQ (rw) : ORIGIN = mboot_req_start, LENGTH = MBOOT_REQ_SIZE
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
