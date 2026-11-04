/*
 * SRAM as the MCUboot bootloader and application share it. The linker symbols come from the CMSIS
 * device header and mboot_request.h (mboot_sram.mk runs the C preprocessor over this file).
 * The request region, which the application writes for the bootloader, is the last
 * MBOOT_REQ_REGION_SIZE bytes of the SRAM and lies outside the RAM of both. The result goes to
 * the linker before the memory script of the bootloader (mboot.ld) and of the application
 * (boards/stm32h573xi_mboot.ld, boards/PYBD_SF6/f767_mboot.ld).
 *
 * STM32H5: SRAM1, SRAM2 and SRAM3 are one contiguous block.
 * STM32F767: the 128 KiB DTCM RAM, SRAM1 (368 KiB) and SRAM2 (16 KiB) are one contiguous block
 * from the DTCM base; the CMSIS header has the base addresses and not the sizes of SRAM2.
 */

#if defined(STM32H5)

MBOOT_SRAM_START = SRAM1_BASE_NS;
MBOOT_SRAM_END = SRAM3_BASE_NS + SRAM3_SIZE;

#elif defined(STM32F767xx)

MBOOT_SRAM_START = RAMDTCM_BASE;
MBOOT_SRAM_END = SRAM2_BASE + 0x4000;

#else

#error "mboot_sram.ld.S: the SRAM of this device is not described"

#endif

MBOOT_REQ_SIZE = MBOOT_REQ_REGION_SIZE;
mboot_req_start = MBOOT_SRAM_END - MBOOT_REQ_SIZE;

#if defined(STM32H5)
ASSERT(SRAM1_BASE_NS + SRAM1_SIZE == SRAM2_BASE_NS && SRAM2_BASE_NS + SRAM2_SIZE == SRAM3_BASE_NS,
    "SRAM1, SRAM2 and SRAM3 are not contiguous")
#else
ASSERT(RAMDTCM_BASE + 0x20000 == SRAM1_BASE, "the DTCM RAM and SRAM1 are not contiguous")
#endif
