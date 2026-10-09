# Makefile of the STM32 MCUboot bootloader (MBOOT_ROLE=bootloader), for STM32H5 boards and for
# STM32F7 boards whose mpconfigboard.h has the MBOOT_* settings (PYBD_SF6).
#
#   make BOARD=NUCLEO_H563ZI                  builds build-NUCLEO_H563ZI/firmware.{elf,bin,hex}
#   make BOARD=NUCLEO_H563ZI MBOOT_CLK=hsi  keeps the reset clock instead of the board PLL clock
#   make BOARD=NUCLEO_H563ZI MBOOT_TEST_FI=1  adds the fault-injection hook for the test campaigns
#                                             (STM32H5 only)
#   make BOARD=NUCLEO_H563ZI PROBE=<serial> deploy-bootloader   programs the bootloader through pyocd
#
# The board's mpconfigboard.h holds the MBOOT_* settings (slot placement and the choices
# listed in shared/mboot/include/mboot_layout.h), mboot_dev.h here describes the flash.

# Select the board to build for:
ifdef BOARD_DIR
# Custom board path - remove trailing slash and get the final component of the path as
# the board name.
BOARD ?= $(notdir $(BOARD_DIR:/=))
else
BOARD ?= NUCLEO_H563ZI
BOARD_DIR ?= $(abspath ../../boards/$(BOARD))
endif

# If the build directory is not given, make it reflect the board name.
BUILD ?= build-$(BOARD)

ifeq ($(wildcard $(BOARD_DIR)/.),)
$(error Invalid BOARD specified: $(BOARD_DIR))
endif

# stm32.mk selects the soft-float flags of the bootloader with this.
BUILDING_MBOOT = 1

include ../../../../py/mkenv.mk
include $(BOARD_DIR)/mpconfigboard.mk

ifeq ($(filter $(MCU_SERIES),h5 f7),)
$(error the MCUboot bootloader of the stm32 port is implemented for STM32H5 and STM32F7 only, board $(BOARD) is MCU_SERIES=$(MCU_SERIES))
endif
ifeq ($(MCU_SERIES)$(MBOOT_TEST_FI),f71)
$(error MBOOT_TEST_FI=1 is implemented for STM32H5 only (tests/mboot/hw/fi_flash.c programs the STM32H5 flash controller), board $(BOARD) is MCU_SERIES=$(MCU_SERIES))
endif

CROSS_COMPILE ?= arm-none-eabi-

include ../../stm32.mk

# Layout files, key table and the source lists of shared/mboot.
MBOOT_ROLE = bootloader
MBOOT_DEV_DIR = $(CURDIR)
# mboot_dev.h reads the CMSIS device header, so the layout generator needs these to preprocess
# it. The port directory is for boards whose mpconfigboard.h includes another board's.
MBOOT_CPP_FLAGS = $(CFLAGS_MCU_$(MCU_SERIES)) -D$(CMSIS_MCU) -I$(TOP)/lib/CMSIS_6/CMSIS/Core/Include -I$(STM32LIB_CMSIS_ABS)/Include -I$(TOP)/ports/stm32
include $(TOP)/shared/mboot/mboot_common.mk

# Clock of the bootloader: pll uses the clock of the board (MICROPY_HW_CLK_* in
# mpconfigboard.h), hsi stays on the reset clock.
MBOOT_CLK ?= pll

INC += -I.
# py/mphal.h includes the mphalport.h of mboot, which also names the pins of the board file.
INC += -I..
INC += -I../..
INC += -I$(TOP)
INC += -I$(BUILD)
INC += -I$(BOARD_DIR)
INC += -I$(TOP)/lib/CMSIS_6/CMSIS/Core/Include
INC += -I$(STM32LIB_CMSIS_ABS)/Include
INC += -I$(STM32LIB_HAL_ABS)/Inc
INC += -I$(TOP)/lib/tinyusb/src
INC += $(MBOOT_INC)

# SRAM linker symbols, from the CMSIS header.
MBOOT_SRAM_LD = $(BUILD)/mboot_sram.ld
MBOOT_CMSIS_H = stm32$(MCU_SERIES)xx.h

# Standard C functions like memset need to be compiled with special flags so the compiler
# does not optimise these functions in terms of themselves.
CFLAGS_BUILTIN ?= -ffreestanding -fno-builtin -fno-lto

CFLAGS += $(INC) -Wall -Wpointer-arith -Wdouble-promotion -Wfloat-conversion -Werror -std=gnu99 $(CFLAGS_EXTRA)
CFLAGS += -D$(CMSIS_MCU) -DUSE_FULL_LL_DRIVER
CFLAGS += $(CFLAGS_MCU_$(MCU_SERIES))
CFLAGS += $(COPT)
CFLAGS += -DBOARD_$(BOARD) -DBUILDING_MBOOT=1 -DMICROPY_HW_STM32WB_FLASH_SYNCRONISATION=0 -DSTM32_HAL_H='<stm32$(MCU_SERIES)xx_hal.h>'
CFLAGS += $(MBOOT_CFLAGS)
CFLAGS += -DMBOOT_STM32_PLL=$(if $(filter hsi,$(MBOOT_CLK)),0,1)

# Remove uncalled code from the final image.
CFLAGS += -fdata-sections -ffunction-sections
LDFLAGS += -nostdlib -L . -T$(MBOOT_LD_LAYOUT) -T$(MBOOT_SRAM_LD) -Tmboot.ld -Map=$(BUILD)/firmware.map --cref --gc-sections --no-warn-rwx-segments
LIBS += $(shell $(CC) $(CFLAGS) -print-libgcc-file-name)

ifeq ($(DEBUG),1)
CFLAGS += -g
COPT = -Og
else
CFLAGS += -g
# -O3 does not build tinycrypt (uECC_vli_mult).
COPT ?= -Os -DNDEBUG
endif

# Bootutil and tinycrypt are third party code; their warnings do not fail the build.
$(BUILD)/lib/mcuboot/%.o: CFLAGS += -Wno-error
$(BUILD)/shared/libc/string0.o: CFLAGS += $(CFLAGS_BUILTIN)

SRC_C = \
	main.c \
	port_common.c \
	port_core.c \
	port_flash.c \
	shared/libc/string0.c \
	ports/stm32/flash.c \
	$(MBOOT_SRC_C)

# A SPI flash of the board (MBOOT_SPIFLASH_* in mpconfigboard.h) uses the drivers of mboot.
ifeq ($(MBOOT_SPIFLASH_ENABLE),1)
SRC_C += drivers/bus/softspi.c drivers/bus/softqspi.c drivers/memory/spiflash.c \
	ports/stm32/qspi.c ports/stm32/spibdev.c ports/stm32/xspi.c $(wildcard $(BOARD_DIR)/*.c)
endif

# The flash driver of the port (flash.c) is the HAL flash driver.
SRC_HAL = $(addprefix lib/stm32lib/STM32$(MCU_SERIES_UPPER)xx_HAL_Driver/Src/stm32$(MCU_SERIES)xx_hal_, flash.c flash_ex.c)

# The fault-injection builds of the hardware tests program the flash with a register level driver
# that can be reset inside the program pulse.
ifeq ($(MBOOT_TEST_FI),1)
SRC_C += tests/mboot/hw/fi_flash.c
endif

# The USB device controller driver (the USB DRD FS of the STM32H5, the DWC2 OTG core of the
# STM32F7); the TinyUSB core and DFU class are listed by mboot_common.mk.
SRC_TINYUSB_C =
ifeq ($(MBOOT_DFU_ENABLE),1)
SRC_C += port_usb.c
SRC_TINYUSB_C += $(MBOOT_TINYUSB_SRC_C)
ifeq ($(MCU_SERIES),h5)
SRC_TINYUSB_C += $(addprefix lib/tinyusb/src/, \
	portable/st/stm32_fsdev/dcd_stm32_fsdev.c \
	portable/st/stm32_fsdev/fsdev_common.c \
	)
else
SRC_TINYUSB_C += $(addprefix lib/tinyusb/src/, \
	portable/synopsys/dwc2/dcd_dwc2.c \
	portable/synopsys/dwc2/dwc2_common.c \
	)
endif
endif

# The vector table and the weak default handlers come from the ST startup file; its
# Reset_Handler is replaced by ports/stm32/resethandler.s, which calls stm32_main().
SRC_O = \
	$(STARTUP_FILE) \
	ports/stm32/resethandler.o

OBJ += $(addprefix $(BUILD)/, $(SRC_O))
OBJ += $(addprefix $(BUILD)/, $(SRC_C:.c=.o))
OBJ += $(addprefix $(BUILD)/, $(SRC_TINYUSB_C:.c=.o))
OBJ += $(addprefix $(BUILD)/, $(SRC_HAL:.c=.o))
OBJ += $(MBOOT_GEN_SRC_C:.c=.o)

all: $(BUILD)/firmware.bin $(BUILD)/firmware.hex

include mboot_sram.mk

$(BUILD)/firmware.elf: $(OBJ) mboot.ld $(MBOOT_SRAM_LD) $(MBOOT_LD_LAYOUT)
	$(ECHO) "LINK $@"
	$(Q)$(LD) $(LDFLAGS) -o $@ $(OBJ) $(LIBS)
	$(Q)$(SIZE) $@

# The gap between the code and the info block at the end of the boot area reads 0xFF.
$(BUILD)/firmware.bin: $(BUILD)/firmware.elf
	$(ECHO) "GEN $@"
	$(Q)$(OBJCOPY) -O binary --gap-fill 0xff $^ $@

$(BUILD)/firmware.hex: $(BUILD)/firmware.elf
	$(ECHO) "GEN $@"
	$(Q)$(OBJCOPY) -O ihex $^ $@

#########################################
# The pin names of the board file (pin_B0 and the aliases of its pins.csv), as in mboot

MAKE_PINS = ../../boards/make-pins.py
PREFIX_FILE = ../../boards/stm32f4xx_prefix.c
BOARD_PINS = $(BOARD_DIR)/pins.csv
HEADER_BUILD = $(BUILD)/genhdr
GEN_PINS_SRC = $(BUILD)/pins_$(BOARD).c
GEN_PINS_HDR = $(HEADER_BUILD)/pins.h
GEN_PINS_AF_CONST = $(HEADER_BUILD)/pins_af_const.h
GEN_PINS_AF_DEFS = $(HEADER_BUILD)/pins_af_defs.h

$(OBJ): $(GEN_PINS_AF_DEFS)

$(HEADER_BUILD):
	$(MKDIR) -p $@

# The SPI flash drivers include py/mperrno.h, which includes the runtime headers that need the
# generated qstr and root pointer headers. The bootloader has no runtime, so they are empty.
ifeq ($(MBOOT_SPIFLASH_ENABLE),1)
GEN_QSTRDEFS_GENERATED = $(HEADER_BUILD)/qstrdefs.generated.h
GEN_ROOT_POINTERS = $(HEADER_BUILD)/root_pointers.h
$(OBJ): $(GEN_QSTRDEFS_GENERATED) $(GEN_ROOT_POINTERS)

$(GEN_QSTRDEFS_GENERATED) $(GEN_ROOT_POINTERS): | $(HEADER_BUILD)
	$(Q)echo "// empty" > $@
endif

$(GEN_PINS_AF_DEFS): $(BOARD_PINS) $(MAKE_PINS) ../../$(AF_FILE) $(PREFIX_FILE) | $(HEADER_BUILD)
	$(ECHO) "GEN $@"
	$(Q)$(PYTHON) $(MAKE_PINS) --board-csv $(BOARD_PINS) --af-csv ../../$(AF_FILE) --prefix $(PREFIX_FILE) \
		--output-source $(GEN_PINS_SRC) --output-header $(GEN_PINS_HDR) \
		--output-af-const $(GEN_PINS_AF_CONST) --output-af-defs $(GEN_PINS_AF_DEFS) \
		--mboot-mode

#########################################

# Programming: the debug probe is always named by serial number, there is no default. The
# pyocd target comes from PYOCD_TARGET in the board's mpconfigboard.mk. Only the sectors the
# image occupies are erased and the option bytes are left alone.
PYOCD ?= pyocd

.PHONY: deploy-bootloader
deploy-bootloader: $(BUILD)/firmware.hex
	$(if $(PROBE),,$(error PROBE=<serial number of the debug probe> is required))
	$(if $(PYOCD_TARGET),,$(error PYOCD_TARGET=<pyocd target of the board> is required))
	$(ECHO) "Writing $< to the board with probe $(PROBE)"
	$(Q)$(PYOCD) flash --erase sector -u $(PROBE) -t $(PYOCD_TARGET) $<

include $(TOP)/shared/mboot/mboot_rules.mk

#########################################
# Compilation rules

vpath %.s . $(TOP)
$(BUILD)/%.o: %.s
	$(ECHO) "AS $<"
	$(Q)$(AS) -o $@ $<

define compile_c
$(ECHO) "CC $<"
$(Q)$(CC) $(CFLAGS) -c -MD -o $@ $<
@# The following fixes the dependency file.
@# See http://make.paulandlesley.org/autodep.html for details.
@# Regex adjusted from the above to play better with Windows paths, etc.
@$(CP) $(@:.o=.d) $(@:.o=.P); \
  $(SED) -e 's/#.*//' -e 's/^.*:  *//' -e 's/ *\\$$//' \
      -e '/^$$/ d' -e 's/$$/ :/' < $(@:.o=.d) >> $(@:.o=.P); \
  $(RM) -f $(@:.o=.d)
endef

vpath %.c . $(TOP)
$(BUILD)/%.o: %.c
	$(call compile_c)

# $(sort $(var)) removes duplicates
#
# The net effect of this, is it causes the objects to depend on the
# object directories (but only for existence), and the object directories
# will be created if they don't exist.
OBJ_DIRS = $(sort $(dir $(OBJ)))
$(OBJ): | $(OBJ_DIRS)
$(OBJ_DIRS):
	$(MKDIR) -p $@

clean:
	$(RM) -rf $(BUILD) $(CLEAN_EXTRA)
.PHONY: clean

-include $(OBJ:.o=.P)
