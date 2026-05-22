/*
** ###################################################################
** Linker script inspired by NXP linker script for MIMXRT10xx
**
** Copyright for original linker script:
**     Copyright 2016 Freescale Semiconductor, Inc.
**     Copyright 2016-2018 NXP
**     SPDX-License-Identifier: BSD-3-Clause
**
**     http:                 www.nxp.com
**     mail:                 support@nxp.com
**
** Integrated ideas from CircuitPython:
** SPDX-License-Identifier: The MIT License (MIT)
** SPDX-FileCopyrightText: Copyright (c) 2020 Scott Shawcroft (tannewt)
**
** Copyright (c) 2021 Philipp Ebensberger
**
** ###################################################################
*/

/* Entry Point */
ENTRY(Reset_Handler)

HEAP_SIZE  = DEFINED(__heap_size__)  ? __heap_size__  : 0x0400;
STACK_SIZE = DEFINED(__stack_size__) ? __stack_size__ : 0x0400;

/* Reserved Area
 * Users can create a reserved area at the end of the flash memory via
 * 'reserved_size' variable. The size of the reserved area should be a multiple
 * of the sector size of the flash memory!
 */
reserved_size = DEFINED(reserved_size) ? reserved_size : 0k;

/* Specify the memory areas */
MEMORY
{
  m_flash_config (RX) : ORIGIN = flash_config_start,    LENGTH = flash_config_size
  m_ivt          (RX) : ORIGIN = ivt_start,             LENGTH = ivt_size
  m_interrupts   (RX) : ORIGIN = interrupts_start,      LENGTH = interrupts_size
  m_text         (RX) : ORIGIN = text_start,            LENGTH = text_size
#if !defined(BUILDING_MBOOT)
  m_vfs          (RX) : ORIGIN = vfs_start,             LENGTH = vfs_size
  /* Teensy uses the last bit of flash for recovery. */
  m_reserved     (RX) : ORIGIN = (vfs_start + vfs_size), LENGTH = reserved_size
#endif
  m_isr          (RX) : ORIGIN = itcm_start,            LENGTH = 0x400
  m_itcm         (RX) : ORIGIN = itcm_start + 0x400,    LENGTH = itcm_size - 0x400
  m_dtcm         (RW) : ORIGIN = dtcm_start,            LENGTH = dtcm_size
  m_ocrm         (RW) : ORIGIN = ocrm_start,            LENGTH = ocrm_size

  #if MICROPY_HW_SDRAM_AVAIL
  m_sdram        (RX) : ORIGIN = sdram_start,           LENGTH = sdram_size
  #endif
}

/* Define output sections */
SECTIONS
{
  __flash_start = flash_start;
  #if MICROPY_HW_SDRAM_AVAIL
  __sdram_start = sdram_start;
  #endif
#if !defined(BUILDING_MBOOT)
  __vfs_start = ORIGIN(m_vfs);
  __vfs_end = __vfs_start + LENGTH(m_vfs);
#endif

  .flash_config :
  {
    . = ALIGN(4);
    __FLASH_BASE = .;
    KEEP(* (.boot_hdr.conf))     /* flash config section */
    . = ALIGN(4);
  } > m_flash_config

  ivt_begin = ORIGIN(m_flash_config) + LENGTH(m_flash_config);

  .ivt : AT(ivt_begin)
  {
    . = ALIGN(4);
    KEEP(* (.boot_hdr.ivt))           /* ivt section */
    KEEP(* (.boot_hdr.boot_data))     /* boot section */
#if !defined(BUILDING_MBOOT)
    KEEP(* (.boot_hdr.dcd_data))      /* dcd section (mboot disables DCD) */
#endif
    . = ALIGN(4);
  } > m_ivt

  /* ISR Vector table in flash. Copied to ITCM by ResetHandler(). */

  .interrupts :
  {
    __Vectors = .;
    __vector_table = .;
    . = ALIGN(4);
    KEEP(*(.isr_vector))     /* Startup code */
    . = ALIGN(4);
  } > m_interrupts

  __Vectors_RAM = ORIGIN(m_isr);

  /* Some program code and other data goes into internal RAM.
   *
   * Files in the EXCLUDE_FILE list have their .text* placed in .ram_functions
   * (ITCM) below.  The two lists differ between the main application build
   * and the mboot build because each hot path is different:
   *   - main app: GC, VM, runtime, map, mpirq, machine_i2c_target need RAM speed
   *   - mboot:    USB device-stack files (usbd, usbd_control, dcd_ci_hs,
   *               dfu_device) must be in ITCM to avoid I-Cache miss fills
   *               racing with USB DMA on the AHB bus.
   *   - both:     fsl_flexspi.o must run from ITCM (AHB flash-access conflict
   *               during write).
   */
  .text :
  {
    . = ALIGN(4);
#if defined(BUILDING_MBOOT)
    *(EXCLUDE_FILE(*fsl_flexspi.o *usbd.o *usbd_control.o *dcd_ci_hs.o *dfu_device.o) .text*)
#else
    *(EXCLUDE_FILE(*fsl_flexspi.o *gc.o *vm.o *runtime*.o *map.o *mpirq.o *machine_i2c_target.o *fsl_lpi2c.o) .text*)
#endif
    *(.rodata)               /* .rodata sections (constants, strings, etc.) */
    *(.rodata*)              /* .rodata* sections (constants, strings, etc.) */
    *(.glue_7)               /* glue arm to thumb code */
    *(.glue_7t)              /* glue thumb to arm code */
    *(.eh_frame)
    KEEP (*(.init))
    KEEP (*(.fini))
    . = ALIGN(4);
  } > m_text

  .ARM.extab :
  {
    *(.ARM.extab* .gnu.linkonce.armextab.*)
  } > m_text

  .ARM :
  {
    __exidx_start = .;
    *(.ARM.exidx*)
    __exidx_end = .;
  } > m_text

 .ctors :
  {
    __CTOR_LIST__ = .;
    /* gcc uses crtbegin.o to find the start of
       the constructors, so we make sure it is
       first.  Because this is a wildcard, it
       doesn't matter if the user does not
       actually link against crtbegin.o; the
       linker won't look for a file to match a
       wildcard.  The wildcard also means that it
       doesn't matter which directory crtbegin.o
       is in.  */
    KEEP (*crtbegin.o(.ctors))
    KEEP (*crtbegin?.o(.ctors))
    /* We don't want to include the .ctor section from
       from the crtend.o file until after the sorted ctors.
       The .ctor section from the crtend file contains the
       end of ctors marker and it must be last */
    KEEP (*(EXCLUDE_FILE(*crtend?.o *crtend.o) .ctors))
    KEEP (*(SORT(.ctors.*)))
    KEEP (*(.ctors))
    __CTOR_END__ = .;
  } > m_text

  .dtors :
  {
    __DTOR_LIST__ = .;
    KEEP (*crtbegin.o(.dtors))
    KEEP (*crtbegin?.o(.dtors))
    KEEP (*(EXCLUDE_FILE(*crtend?.o *crtend.o) .dtors))
    KEEP (*(SORT(.dtors.*)))
    KEEP (*(.dtors))
    __DTOR_END__ = .;
  } > m_text

  .preinit_array :
  {
    PROVIDE_HIDDEN (__preinit_array_start = .);
    KEEP (*(.preinit_array*))
    PROVIDE_HIDDEN (__preinit_array_end = .);
  } > m_text

  .init_array :
  {
    PROVIDE_HIDDEN (__init_array_start = .);
    KEEP (*(SORT(.init_array.*)))
    KEEP (*(.init_array*))
    PROVIDE_HIDDEN (__init_array_end = .);
  } > m_text

  .fini_array :
  {
    PROVIDE_HIDDEN (__fini_array_start = .);
    KEEP (*(SORT(.fini_array.*)))
    KEEP (*(.fini_array*))
    PROVIDE_HIDDEN (__fini_array_end = .);
  } > m_text

  __etext = .;    /* define a global symbol at end of code */
  __DATA_ROM = .; /* Symbol is used by startup for data initialization */

  .data : AT(__DATA_ROM)
  {
    . = ALIGN(4);
    __DATA_RAM = .;
    __data_start__ = .;      /* create a global symbol at data start */
    __data_section_table = .;
#if !defined(BUILDING_MBOOT)
    *(m_usb_dma_init_data)
#endif
    *(.data)                 /* .data sections */
    *(.data*)                /* .data* sections */
    KEEP(*(.jcr*))
    . = ALIGN(4);
    __data_end__ = .;        /* define a global symbol at data end */
    __data_section_table_end = .;
  } > m_dtcm

  __RAM_FUNCTIONS_ROM = __DATA_ROM + (__data_end__ - __data_start__);
  .ram_functions : AT(__RAM_FUNCTIONS_ROM)
  {
    . = ALIGN(4);
    __ram_function_start__ = .;
    *(.ram_functions*)
#if defined(BUILDING_MBOOT)
    /* TinyUSB USB event-processing hot paths: placed in ITCM to eliminate
     * I-Cache miss fills that could race with USB DMA on the AHB bus.
     * These files are excluded from .text above and collected here instead. */
    *usbd.o(.text*)
    *usbd_control.o(.text*)
    *dcd_ci_hs.o(.text*)
    *dfu_device.o(.text*)
#else
    /* remaining .text and .rodata; i.e. stuff we exclude above because we want it in RAM */
    *(.text*)
#endif
    . = ALIGN(4);
    __ram_function_end__ = .;
  } > m_itcm

  __NDATA_ROM = __RAM_FUNCTIONS_ROM + (__ram_function_end__ - __ram_function_start__);
#if !defined(BUILDING_MBOOT)
  .ncache.init : AT(__NDATA_ROM)
  {
    __noncachedata_start__ = .;   /* create a global symbol at ncache data start */
    *(NonCacheable.init)
    . = ALIGN(4);
    __noncachedata_init_end__ = .;   /* create a global symbol at initialized ncache data end */
  } > m_dtcm
  . = __noncachedata_init_end__;
  .ncache :
  {
    *(NonCacheable)
    . = ALIGN(4);
    __noncachedata_end__ = .;     /* define a global symbol at ncache data end */
  } > m_dtcm

  __DATA_END = __NDATA_ROM + (__noncachedata_end__ - __noncachedata_start__);
  __FLASH_DATA_END = __NDATA_ROM + (__noncachedata_init_end__ - __noncachedata_start__);
#else
  __DATA_END = __NDATA_ROM;
  __FLASH_DATA_END = __NDATA_ROM;
#endif
  text_end = ORIGIN(m_text) + LENGTH(m_text);
  ASSERT(__DATA_END <= text_end, "region m_text overflowed with text and data")

  /* Uninitialized data section */
  .bss :
  {
    /* This is used by the startup in order to initialize the .bss section */
    . = ALIGN(4);
    __START_BSS = .;
    __bss_start__ = .;
    __bss_section_table = .;
#if !defined(BUILDING_MBOOT)
    *(m_usb_dma_noninit_data)
#endif
    *(.bss)
    *(.bss*)
    *(COMMON)
    . = ALIGN(4);
    __bss_end__ = .;
    __bss_section_table_end = .;
    __END_BSS = .;
  } > m_dtcm

#if defined(BUILDING_MBOOT)
  /* USB DMA buffers: must be in OCRAM so the USB AHB bus master can access
   * them.  NOLOAD: these are zeroed/initialised by TinyUSB at runtime
   * (dcd_init, bus_reset).  The main application allocates equivalent
   * buffers via the .ncache section instead. */
  .usb_dma_noninit (NOLOAD) :
  {
    . = ALIGN(2048);
    *(.usb_dma_noninit*)
    . = ALIGN(4);
  } > m_ocrm
#endif

  .heap :
  {
    . = ALIGN(8);
    __end__ = .;
    PROVIDE(end = .);
    __HeapBase = .;
    . += HEAP_SIZE;
    __HeapLimit = .;
    __heap_limit = .; /* Add for _sbrk */
  } > m_dtcm

  .stack :
  {
    . = ALIGN(8);
    . += STACK_SIZE;
  } > m_dtcm

  _flashimagelen = __FLASH_DATA_END - flash_start;

  /* Initializes stack on the end of block */
  __StackTop   = ORIGIN(m_dtcm) + LENGTH(m_dtcm);
  __StackLimit = __StackTop - STACK_SIZE;
  _vStackTop = __StackTop;
#if defined(BUILDING_MBOOT)
  _estack      = __StackTop;
  _sstack      = __StackLimit;
#endif
  PROVIDE(__stack = __StackTop);

  .ARM.attributes 0 : { *(.ARM.attributes) }

  ASSERT(__StackLimit >= __HeapLimit, "region m_dtcm overflowed with stack and heap")
}
