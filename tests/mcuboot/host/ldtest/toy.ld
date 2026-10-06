/*
 * Toy port script: the layout symbols (mcuboot_layout.ld, in front of this script) place the
 * image in the primary slot after the image header, as the application scripts of a port do.
 */

MEMORY
{
    FLASH (rx) : ORIGIN = MCUBOOT_APP_START, LENGTH = MCUBOOT_APP_LEN
    RAM (rwx)  : ORIGIN = 0x20000000, LENGTH = 64K
}

toy_stack_top = ORIGIN(RAM) + LENGTH(RAM);

SECTIONS
{
    .isr_vector : { KEEP(*(.isr_vector)) } >FLASH
    .text : { *(.text*) *(.rodata*) toy_text_end = .; } >FLASH
    .data : { toy_data_start = .; *(.data*) toy_data_end = .; } >RAM AT >FLASH
    /* The primary slot starts with the header that imgtool prepends. */
    toy_image_start = MCUBOOT_PRIMARY_START;
    toy_app_size = MCUBOOT_APP_LEN;
}

ASSERT(toy_text_end <= MCUBOOT_PRIMARY_START + MCUBOOT_MAX_IMAGE_SIZE, "the image does not fit in the primary slot")
