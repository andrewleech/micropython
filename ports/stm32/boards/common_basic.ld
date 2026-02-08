/* Memory layout for basic configuration:

    FLASH       .isr_vector
    FLASH       .text
    FLASH       .data

    RAM         .data
    RAM         .bss
    RAM         .heap
    RAM         .stack
*/

ENTRY(Reset_Handler)

REGION_ALIAS("FLASH_ISR", FLASH);
REGION_ALIAS("FLASH_COMMON", FLASH);

/* define output sections */
SECTIONS
{
    INCLUDE common_isr.ld
    INCLUDE common_text.ld
    INCLUDE common_extratext_data_in_flash.ld
    INCLUDE common_bss_heap_stack.ld

    /* STM32WB55 IPCC sections - only used if RAM2A/RAM2B regions are defined */
    .ram2a_bss (NOLOAD) :
    {
        . = ALIGN(4);
        . = . + 64; /* Leave room for the mb_ref_table_t (assuming IPCCDBA==0). */
        *rfcore.o(.bss.ipcc_mem_*)
        . = ALIGN(4);
    } >RAM2A

    .ram2b_bss (NOLOAD) :
    {
        . = ALIGN(4);
        *rfcore.o(.bss.ipcc_membuf_*)
        . = ALIGN(4);
    } >RAM2B
}
