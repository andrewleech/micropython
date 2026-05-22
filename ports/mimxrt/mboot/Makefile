# This file is part of the MicroPython project, http://micropython.org/
#
# The MIT License (MIT)
#
# Copyright (c) 2026 Andrew Leech
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
# THE SOFTWARE.

# =============================================================================
# Board selection
# =============================================================================

ifdef BOARD_DIR
BOARD ?= $(notdir $(BOARD_DIR:/=))
else
BOARD ?= MIMXRT1010_EVK
# Use the main mimxrt board dir so the per-board mboot config can live
# alongside the existing mpconfigboard.{h,mk} files instead of being
# duplicated under ports/mimxrt/mboot/boards/.
BOARD_DIR ?= ../boards/$(BOARD)
endif

ifeq ($(wildcard $(BOARD_DIR)/.),)
$(error Invalid BOARD specified: $(BOARD_DIR))
endif

BUILD ?= build-$(BOARD)
CROSS_COMPILE ?= arm-none-eabi-

# Enable BUILDING_MBOOT so board .mk files can gate mboot-specific settings.
BUILDING_MBOOT = 1

# =============================================================================
# Include make environment and board config
# =============================================================================

include ../../../py/mkenv.mk
include $(BOARD_DIR)/mpconfigboard.mk

# The board's mpconfigboard.mk should set `USE_MBOOT ?= 1` to opt in.  Check
# this after the include so the value defined in the board's .mk is visible.
USE_MBOOT ?= 0
ifneq ($(USE_MBOOT),1)
$(error Board $(BOARD) does not enable mboot (set USE_MBOOT=1 in its mpconfigboard.mk))
endif

TOP := $(abspath ../../..)

# =============================================================================
# Paths
# =============================================================================

PORT_DIR    := $(TOP)/ports/mimxrt
MCUX_SDK    := $(TOP)/lib/nxp_driver/sdk
MCU_DIR     := $(MCUX_SDK)/devices/$(MCU_SERIES)
TUSB_DIR    := $(TOP)/lib/tinyusb/src
CMSIS_INC   := $(TOP)/lib/CMSIS_6/CMSIS/Core/Include

# =============================================================================
# Linker script
# =============================================================================

# mboot reuses the main mimxrt linker scripts (chip family + common.ld);
# BUILDING_MBOOT gates mboot-specific behaviour inside those files.
LD_FILES := ../boards/$(MCU_SERIES).ld ../boards/common.ld

# mboot slot size and flash size are passed as C-preprocessor defines so the
# linker script can reference them.  The names must match the linker script.
LDDEFINES += \
    -DBUILDING_MBOOT=$(BUILDING_MBOOT) \
    -DMICROPY_HW_FLASH_SIZE=$(MICROPY_HW_FLASH_SIZE) \
    -DMICROPY_HW_FLASH_TYPE=$(MICROPY_HW_FLASH_TYPE) \
    -DMICROPY_HW_ROMFS_BYTES=0 \
    -DMBOOT_FLASH_SIZE=$(MBOOT_FLASH_SIZE) \
    -DMICROPY_HW_FLASH_RESERVED=$(MBOOT_FLASH_SIZE)

# =============================================================================
# Includes
# =============================================================================

INC += -I.
INC += -I$(BOARD_DIR)
INC += -I$(BUILD)
INC += -I$(TOP)
INC += -I$(CMSIS_INC)
INC += -I$(PORT_DIR)
INC += -I$(PORT_DIR)/hal
INC += -I$(PORT_DIR)/boards
INC += -I$(TUSB_DIR)
INC += -I$(TUSB_DIR)/..                      # for tusb.h pulling hw/bsp headers
INC += -I$(MCU_DIR)
INC += -I$(MCU_DIR)/drivers
INC += -I$(MCU_DIR)/xip
INC += -I$(MCUX_SDK)/drivers/common
INC += -I$(MCUX_SDK)/drivers/flexspi
INC += -I$(MCUX_SDK)/drivers/igpio
INC += -I$(MCUX_SDK)/drivers/wdog01

# RT1176 has its cache driver under drivers/cm7.
ifeq ($(MCU_SERIES), MIMXRT1176)
INC += -I$(MCU_DIR)/drivers/cm7
endif

# Include the shared mboot fragment.
include $(TOP)/shared/tinyusb/mboot/mboot_common.mk
INC     += $(SHARED_TINYUSB_MBOOT_INC)

# =============================================================================
# Source files
# =============================================================================

# Port-specific mboot sources.
SRC_C += \
    main.c \
    mboot_port.c \
    port_boot_data.c \
    port_flash.c \
    port_flexspi_nor.c \
    port_regions.c

# Minimal libc replacements (no OS, no RTOS).
# Mirrors stm32/mboot/Makefile:113-114 pattern.
SRC_LIB_C += \
    shared/libc/string0.c

# Shared mboot common sources (mboot_dfu, mboot_region, mboot_elem, mboot_usbd).
SRC_C += $(SHARED_TINYUSB_MBOOT_SRC_C)

# TinyUSB device stack.
SRC_TUSB_C += \
    $(TUSB_DIR)/class/dfu/dfu_device.c \
    $(TUSB_DIR)/class/vendor/vendor_device.c \
    $(TUSB_DIR)/common/tusb_fifo.c \
    $(TUSB_DIR)/device/usbd.c \
    $(TUSB_DIR)/device/usbd_control.c \
    $(TUSB_DIR)/portable/chipidea/ci_hs/dcd_ci_hs.c \
    $(TUSB_DIR)/tusb.c

# NXP SDK startup and system files.
SRC_S += $(MCU_DIR)/gcc/startup_$(MCU_SERIES)$(MCU_CORE).S

SRC_HAL_C += \
    $(MCU_DIR)/system_$(MCU_SERIES)$(MCU_CORE).c \
    $(MCU_DIR)/drivers/fsl_clock.c \
    $(MCUX_SDK)/drivers/common/fsl_common.c \
    $(MCUX_SDK)/drivers/common/fsl_common_arm.c \
    $(MCUX_SDK)/drivers/flexspi/fsl_flexspi.c \
    $(MCUX_SDK)/drivers/igpio/fsl_gpio.c \
    $(MCUX_SDK)/drivers/wdog01/fsl_wdog.c

# Board clock config (generated SDK source, lives alongside board-specific lds).
SRC_HAL_C += $(PORT_DIR)/boards/$(MCU_SERIES)_clock_config.c

# FCB: the shared HAL FCB source, fed per-board MICROPY_HW_FLASH_* macros.
# IVT and BootData are provided by port_boot_data.c (see SRC_C above) which
# sets boot_data.length = _flashimagelen (the actual mboot image size) rather
# than BOARD_FLASH_SIZE (the full flash).  The SDK fsl_flexspi_nor_boot.c is
# NOT linked; it hard-codes BOOT_IMAGE_SIZE = FLASH_SIZE.
SRC_HAL_C += $(PORT_DIR)/hal/qspi_nor_flash_config.c

# RT1176-specific drivers.
ifeq ($(MCU_SERIES), MIMXRT1176)
SRC_HAL_C += \
    $(MCU_DIR)/drivers/cm7/fsl_cache.c \
    $(MCU_DIR)/drivers/fsl_dcdc.c \
    $(MCU_DIR)/drivers/fsl_pmu.c \
    $(MCU_DIR)/drivers/fsl_anatop_ai.c \
    $(MCU_DIR)/drivers/fsl_soc_src.c \
    $(MCU_DIR)/drivers/fsl_gpc.c
INC += \
    -I$(MCU_DIR)/drivers/cm7
endif

# =============================================================================
# Compiler flags
# =============================================================================

CFLAGS += -g -Os -DNDEBUG
CFLAGS += -Wall -Werror -Wdouble-promotion -Wfloat-conversion
CFLAGS += -Wno-error=unused-parameter
CFLAGS += -std=c99
CFLAGS += -fdata-sections -ffunction-sections
CFLAGS += -mcpu=cortex-m7 -mthumb -mtune=cortex-m7
CFLAGS += -mfloat-abi=hard -mfpu=fpv5-d16
CFLAGS += -ffreestanding -fno-builtin

CFLAGS += $(INC)

CFLAGS += \
    -D__START=main \
    -D__STARTUP_CLEAR_BSS \
    -D__STARTUP_INITIALIZE_RAMFUNCTION \
    -DBOARD_$(BOARD) \
    -DBOARD_FLASH_SIZE=$(MICROPY_HW_FLASH_SIZE) \
    -DBUILDING_MBOOT=$(BUILDING_MBOOT) \
    -DCFG_TUSB_MCU=OPT_MCU_MIMXRT1XXX \
    -DCLOCK_CONFIG_H='<boards/$(MCU_SERIES)_clock_config.h>' \
    -DCPU_$(MCU_VARIANT)$(MCU_CORE) \
    -DCPU_$(MCU_VARIANT) \
    -DCPU_HEADER_H='<$(MCU_SERIES)$(MCU_CORE).h>' \
    -DFSL_SDK_ENABLE_DRIVER_CACHE_CONTROL=1 \
    -DMICROPY_HW_FLASH_SIZE=$(MICROPY_HW_FLASH_SIZE) \
    -DMBOOT_FLASH_SIZE=$(MBOOT_FLASH_SIZE) \
    -DUSE_MBOOT \
    -DXIP_BOOT_HEADER_ENABLE=1 \
    -DXIP_EXTERNAL_FLASH=1 \
    -DXIP_BOOT_HEADER_DCD_ENABLE=0

# Shared functions annotated MBOOT_RAM_FUNC_ATTR must live in RAM because the
# FlexSPI controller suspends XIP during erase / program.  `.ram_functions`
# is collected into ITCM by ports/mimxrt/boards/common.ld.  Contract
# documented in mboot_api.h.
CFLAGS += '-DMBOOT_RAM_FUNC_ATTR=__attribute__((section(".ram_functions")))'

# Board-specific flash config header (used by qspi_nor_flash_config.c).
CFLAGS += -DBOARD_FLASH_CONFIG_HEADER_H=\"hal/flexspi_flash_config.h\"
CFLAGS += -DBOARD_FLASH_OPS_HEADER_H=\"hal/flexspi_nor_flash.h\"

ifdef MICROPY_HW_FLASH_CLK
CFLAGS += -DMICROPY_HW_FLASH_CLK=$(MICROPY_HW_FLASH_CLK)
endif
ifdef MICROPY_HW_FLASH_QE_CMD
CFLAGS += -DMICROPY_HW_FLASH_QE_CMD=$(MICROPY_HW_FLASH_QE_CMD)
endif
ifdef MICROPY_HW_FLASH_QE_ARG
CFLAGS += -DMICROPY_HW_FLASH_QE_ARG=$(MICROPY_HW_FLASH_QE_ARG)
endif

# MCU family tag: used in tusb_config.h for speed selection.
ifeq ($(MCU_SERIES), MIMXRT1176)
CFLAGS += -DMBOOT_MCU_MIMXRT11XX=1
CFLAGS += -DMIMXRT117x_SERIES
CFLAGS += -DUSB1_BASE=USB_OTG1_BASE
CFLAGS += -DUSB2_BASE=USB_OTG2_BASE
else
CFLAGS += -DMBOOT_MCU_MIMXRT10XX=1
endif

# =============================================================================
# Linker flags
# =============================================================================

LDFLAGS += \
    -nostdlib \
    --cref \
    --gc-sections \
    --print-memory-usage \
    -Map=$(BUILD)/firmware.elf.map

LIBS += $(shell $(CC) $(CFLAGS) -print-libgcc-file-name)

# =============================================================================
# Generated header stubs (mirrors stm32/mboot/Makefile:278-282)
#
# Several MicroPython headers included transitively reference qstrdefs and
# root_pointers.  Mboot does not use the MicroPython VM, so these files are
# stubs that satisfy the preprocessor without emitting any real definitions.
# =============================================================================

HEADER_BUILD := $(BUILD)/genhdr
GEN_QSTRDEFS := $(HEADER_BUILD)/qstrdefs.generated.h
GEN_ROOT_PTR := $(HEADER_BUILD)/root_pointers.h

# =============================================================================
# Objects
# =============================================================================

OBJ += $(addprefix $(BUILD)/, $(SRC_C:.c=.o))
OBJ += $(addprefix $(BUILD)/, $(SRC_LIB_C:.c=.o))
OBJ += $(addprefix $(BUILD)/, $(SRC_TUSB_C:.c=.o))
OBJ += $(addprefix $(BUILD)/, $(SRC_HAL_C:.c=.o))
OBJ += $(addprefix $(BUILD)/, $(SRC_S:.S=.o))

# =============================================================================
# Build targets
# =============================================================================

all: $(BUILD)/firmware.bin $(BUILD)/firmware.hex

$(BUILD)/firmware.elf: $(OBJ) | $(GEN_QSTRDEFS) $(GEN_ROOT_PTR)
	$(ECHO) "PREPROCESS LINK $@"
	$(Q)$(CC) -E -x c $(LDDEFINES) $(LD_FILES) | grep -v '^#' > $(BUILD)/link.ld
	$(ECHO) "LINK $@"
	$(Q)$(LD) -T$(BUILD)/link.ld $(LDFLAGS) -o $@ $^ $(LIBS)
	$(Q)$(SIZE) $@

$(BUILD)/firmware.bin: $(BUILD)/firmware.elf
	$(ECHO) "BIN $@"
	$(Q)$(OBJCOPY) -O binary $< $@

# firmware.hex carries absolute flash addresses; flashing tools can use it
# directly without specifying an offset.  For RT1176 (FCB at flash_base+0x400)
# the .hex avoids the offset confusion present when flashing the raw .bin.
$(BUILD)/firmware.hex: $(BUILD)/firmware.elf
	$(ECHO) "HEX $@"
	$(Q)$(OBJCOPY) -O ihex $< $@

# =============================================================================
# Stub header generation
# =============================================================================

$(HEADER_BUILD):
	$(MKDIR) -p $@

$(GEN_QSTRDEFS): | $(HEADER_BUILD)
	$(Q)echo "// empty" > $@

$(GEN_ROOT_PTR): | $(HEADER_BUILD)
	$(Q)echo "// empty" > $@

# =============================================================================
# Compilation rules
# =============================================================================

OBJ_DIRS = $(sort $(dir $(OBJ)))
$(OBJ): | $(OBJ_DIRS) $(GEN_QSTRDEFS) $(GEN_ROOT_PTR)
$(OBJ_DIRS):
	$(MKDIR) -p $@

# Suppress strict-aliasing and missing-field-initializers warnings from NXP SDK.
$(BUILD)/$(MCUX_SDK)/%.o: CFLAGS += -Wno-missing-field-initializers
$(BUILD)/$(TUSB_DIR)/device/usbd.o: CFLAGS += -Wno-missing-braces

vpath %.S . $(TOP)
$(BUILD)/%.o: %.S
	$(ECHO) "AS $<"
	$(Q)$(CC) $(CFLAGS) -c -o $@ $<

vpath %.c . $(TOP)
$(BUILD)/%.o: %.c
	$(ECHO) "CC $<"
	$(Q)$(CC) $(CFLAGS) -include $(BOARD_DIR)/mpconfigboard.h -c -MD -o $@ $<
	@$(CP) $(@:.o=.d) $(@:.o=.P) 2>/dev/null; \
	$(SED) -e 's/#.*//' -e 's/^.*:  *//' -e 's/ *\\$$//' \
	    -e '/^$$/ d' -e 's/$$/ :/' < $(@:.o=.d) >> $(@:.o=.P); \
	$(RM) -f $(@:.o=.d)

clean:
	$(RM) -rf $(BUILD)
.PHONY: clean all

-include $(OBJ:.o=.P)
