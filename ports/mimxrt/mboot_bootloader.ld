/* MIMXRT ROM-bootable Mboot image with internal OCRAM runtime. */
MEMORY
{
    FLASH_CONFIG (rx) : ORIGIN = MBOOT_BOOT_START, LENGTH = MBOOT_FCB_OFFSET + MBOOT_FCB_SIZE
    FLASH_IVT (rx) : ORIGIN = MBOOT_BOOT_START + 0x1000, LENGTH = 0x1000
    FLASH_VECTORS (rx) : ORIGIN = MBOOT_BOOT_START + MBOOT_VECTOR_OFFSET, LENGTH = 0x400
    FLASH_CODE (rx) : ORIGIN = MBOOT_BOOT_START + MBOOT_VECTOR_OFFSET + 0x400,
        LENGTH = MBOOT_BOOT_SIZE - MBOOT_VECTOR_OFFSET - 0x400 - 128
    FLASH_INFO (r) : ORIGIN = MBOOT_BOOT_START + MBOOT_BOOT_SIZE - 128, LENGTH = 128
    RAM (xrw) : ORIGIN = MBOOT_SRAM_START, LENGTH = 128K
}

ENTRY(Reset_Handler)
__FLASH_BASE = MBOOT_BOOT_START;
__Vectors = ORIGIN(FLASH_VECTORS);
__VECTOR_TABLE = ORIGIN(FLASH_VECTORS);
__Vectors_RAM = ORIGIN(RAM);
__mboot_ram_end = ORIGIN(RAM) + LENGTH(RAM);
mboot_status_start = __mboot_ram_end;
mboot_status_end = MBOOT_BACKUP_START;
_minimum_stack_size = 16K;
__StackTop = __mboot_ram_end;
__StackLimit = __StackTop - _minimum_stack_size;
_estack = __StackTop;

SECTIONS
{
    .flash_config :
    {
        FILL(0xffffffff)
        . += MBOOT_FCB_OFFSET;
        __mboot_fcb_start = .;
        KEEP(*(.boot_hdr.conf))
    } > FLASH_CONFIG

    .ivt :
    {
        KEEP(*(.boot_hdr.ivt))
        KEEP(*(.boot_hdr.boot_data))
        KEEP(*(.boot_hdr.dcd_data))
    } > FLASH_IVT

    .isr_vector :
    {
        . = ALIGN(1024);
        KEEP(*(.isr_vector))
        . = ALIGN(4);
    } > FLASH_VECTORS

    .text :
    {
        . = ALIGN(4);
        *(EXCLUDE_FILE(*fsl_flexspi.o *flexspi_nor_flash.o *flash.o *mboot_port.o *fault.o *log.o *printf_lite.o *string0.o *clock_config.o *fsl_clock.o *fsl_common_arm.o *fsl_pmu.o *fsl_anatop_ai.o *libgcc.a:*) .text*)
        *(EXCLUDE_FILE(*fsl_flexspi.o *flexspi_nor_flash.o *flash.o *mboot_port.o *fault.o *log.o *printf_lite.o *string0.o *clock_config.o *fsl_clock.o *fsl_common_arm.o *fsl_pmu.o *fsl_anatop_ai.o *libgcc.a:*) .rodata*)
        *(.glue_7)
        *(.glue_7t)
        KEEP(*(.init))
        KEEP(*(.fini))
        . = ALIGN(4);
    } > FLASH_CODE

    .mboot_vectors (NOLOAD) :
    {
        . = ALIGN(1024);
        __mboot_vectors_start = .;
        . += 0x400;
        __mboot_vectors_end = .;
    } > RAM

    _sidata = LOADADDR(.data);
    .data :
    {
        . = ALIGN(8);
        _sdata = .;
        __data_start__ = .;
        *(.data*)
        . = ALIGN(4);
        _edata = .;
        __data_end__ = .;
    } > RAM AT> FLASH_CODE

    .ram_functions :
    {
        . = ALIGN(4);
        __ram_function_start__ = .;
        *(.ram_functions*)
        *(.ramfunc*)
        *backup_words.o(.text* .rodata*)
        *flash.o(.text* .rodata*)
        *fsl_flexspi.o(.text* .rodata*)
        *flexspi_nor_flash.o(.text* .rodata*)
        *mboot_port.o(.text* .rodata*)
        /* Clock setup can gate the ROM-selected FlexSPI clock before caches are enabled. */
        *clock_config.o(.text* .rodata*)
        *fsl_clock.o(.text* .rodata*)
        *fsl_common_arm.o(.text* .rodata*)
        *fsl_pmu.o(.text* .rodata*)
        *fsl_anatop_ai.o(.text* .rodata*)
        *libgcc.a:*(.text* .rodata*)
        *fault.o(.text* .rodata*)
        *log.o(.text* .rodata*)
        *printf_lite.o(.text* .rodata*)
        *string0.o(.text* .rodata*)
        . = ALIGN(4);
        __ram_function_end__ = .;
    } > RAM AT> FLASH_CODE
    __ram_functions_load__ = LOADADDR(.ram_functions);

    .bss (NOLOAD) :
    {
        . = ALIGN(8);
        _sbss = .;
        *(.bss*)
        *(COMMON)
        . = ALIGN(8);
        _ebss = .;
    } > RAM

    .mboot_usb_dma ALIGN(2048) (NOLOAD) :
    {
        __mboot_usb_dma_start = .;
        KEEP(*(.mboot_usb_dma))
        __mboot_usb_dma_end = .;
    } > RAM

    .stack (NOLOAD) :
    {
        . = ALIGN(8);
        . += _minimum_stack_size;
    } > RAM

    __etext = _sidata;
    _flashimagelen = MBOOT_BOOT_SIZE;

    .mboot_info :
    {
        KEEP(*(.mboot_info))
    } > FLASH_INFO

    .ARM.attributes 0 : { *(.ARM.attributes) }
    /DISCARD/ : { *(.ARM.exidx*) *(.ARM.extab*) }
}

ASSERT(SIZEOF(.flash_config) == MBOOT_FCB_OFFSET + 512, "NXP FCB is missing or has the wrong size")
ASSERT(SIZEOF(.ivt) >= 32, "NXP IVT and boot data are missing")
ASSERT(SIZEOF(.isr_vector) <= 0x400, "Mboot vector table exceeds its reserved flash window")
ASSERT(SIZEOF(.mboot_usb_dma) <= 16K, "USB DMA region is unexpectedly large")
ASSERT((ADDR(.mboot_usb_dma) & 0x7ff) == 0, "USB DMA data is not 2 KiB aligned")
ASSERT(mboot_status_start <= mboot_status_end, "Mboot status window overlaps retained backup state")
ASSERT(MBOOT_REQ_START >= MBOOT_BACKUP_START + 0x400, "Mboot request overlaps backup words")
ASSERT(SIZEOF(.mboot_info) == 128, "Mboot info block is not 128 bytes")
ASSERT(_ebss + _minimum_stack_size <= __mboot_ram_end, "Mboot OCRAM is too small for data, bss and stack")
