# Build fragment of the MCUboot application variant of the stm32 port, included by the
# Makefile after the board's mpconfigboard.mk when MCUBOOT=1. The build directory defaults to
# build-$(BOARD)-mcuboot, or build-$(BOARD)-$(BOARD_VARIANT)-mcuboot, so it does not clash with
# the default firmware build:
#
#   make BOARD=NUCLEO_H563ZI MCUBOOT=1             builds firmware.bin linked into the primary slot
#                                                  and firmware.signed.bin (signed with MCUBOOT_SIGN_KEY,
#                                                  by default the public test key)
#   make BOARD=NUCLEO_H563ZI MCUBOOT=1 initial     firmware.initial.{bin,hex}, the first image of the primary slot
#   make BOARD=NUCLEO_H563ZI MCUBOOT=1 dfu         firmware.signed.dfu for tools/pydfu.py
#   make BOARD=NUCLEO_H563ZI MCUBOOT=1 PROBE=<serial> deploy-initial
#
# The bootloader is built by mcuboot/Makefile. Both take their layout from the MCUBOOT_* settings
# in the board's mpconfigboard.h and from mcuboot/mcuboot_dev.h.

ifeq ($(filter $(MCU_SERIES),h5 f7),)
$(error MCUBOOT=1 is implemented for STM32H5 and STM32F7 only, board $(BOARD) is MCU_SERIES=$(MCU_SERIES))
endif

MCUBOOT_ROLE = app
MCUBOOT_DEV_DIR = $(CURDIR)/mcuboot
# mcuboot_dev.h reads the CMSIS device header, so the layout generator has to preprocess it with
# the cross compiler. The generator runs while this fragment is read, before the Makefile sets
# CROSS_COMPILE and includes stm32.mk (core flags and the CMSIS header location), so this
# fragment does both too. The port directory is for boards whose mpconfigboard.h includes
# another board's.
CROSS_COMPILE ?= arm-none-eabi-
include stm32.mk
MCUBOOT_CPP_FLAGS = $(CFLAGS_MCU_$(MCU_SERIES)) -D$(CMSIS_MCU) -I$(TOP)/lib/CMSIS_6/CMSIS/Core/Include -I$(STM32LIB_CMSIS_ABS)/Include -I$(TOP)/ports/stm32
include $(TOP)/shared/mcuboot/mcuboot_common.mk

# MICROPY_HW_MCUBOOT_APP selects the port hooks: the flash ECC NMI handler, the reset flags
# handoff and the routing of machine.bootloader().

# The firmware starts after the image header in the primary slot. The MCU's linker script is
# replaced by its _mcuboot variant, which takes the addresses from the layout. An MCUboot build
# is never linked for mboot, even with USE_MBOOT=1 on the command line.
override USE_MBOOT = 0
MCUBOOT_SRAM_LD = $(BUILD)/mcuboot_sram.ld
MCUBOOT_CMSIS_H = stm32$(MCU_SERIES)xx.h
LD_FILES := $(MCUBOOT_LD_LAYOUT) $(MCUBOOT_SRAM_LD) $(patsubst %.ld,%_mcuboot.ld,$(firstword $(LD_FILES))) boards/common_bl.ld
TEXT0_ADDR := $(MCUBOOT_APP_LINK_ADDR)

# The include directories of the crypto libraries of bootutil come last (mcuboot_app_rules.mk):
# NimBLE and Mbed TLS of the application have headers of the same names that the application
# sources have to find first.
MCUBOOT_CRYPTO_INC = $(filter %/ext/tinycrypt/lib/include %/ext/mbedtls-asn1/include,$(MCUBOOT_INC))
INC += $(filter-out $(MCUBOOT_CRYPTO_INC),$(MCUBOOT_INC))
CFLAGS += $(MCUBOOT_CFLAGS) -DMICROPY_HW_MCUBOOT_APP=1

# The port files shared with the bootloader: the handoff words, the ECC event counter and the
# flash policy layered over flash.c.
SRC_C += \
	mcuboot/port_common.c \
	mcuboot/port_flash.c

LIB_SRC_C += $(MCUBOOT_SRC_C)
OBJ += $(MCUBOOT_GEN_SRC_C:.c=.o)

# Bootutil is third party code; its warnings do not fail the build.
$(BUILD)/lib/mcuboot/%.o: CFLAGS += -Wno-error
