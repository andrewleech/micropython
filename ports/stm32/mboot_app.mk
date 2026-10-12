# Application build configuration included by a board's mpconfigvariant.mk.
#
#   make BOARD=NUCLEO_H563ZI                 builds firmware.bin in the primary slot
#                                           and firmware.signed.bin (MBOOT_SIGN_KEY)
#   make BOARD=NUCLEO_H563ZI initial         builds firmware.initial.{bin,hex}
#   make BOARD=NUCLEO_H563ZI dfu             builds firmware.signed.dfu
#   make BOARD=NUCLEO_H563ZI PROBE=<serial> deploy-initial
#
# The bootloader is built by Makefile.mboot. Both take their layout from the
# MICROPY_*MBOOT_* settings in the board's mpconfigboard.h and from mboot_dev.h.

ifeq ($(filter $(MCU_SERIES),h5 f7),)
$(error Mboot applications are implemented for STM32H5 and STM32F7 only, board $(BOARD) is MCU_SERIES=$(MCU_SERIES))
endif

MBOOT_ROLE = app
MBOOT_DEV_DIR = $(CURDIR)
# mboot_dev.h reads the CMSIS device header, so the layout generator has to preprocess it with
# the cross compiler. The generator runs while this fragment is read, before the Makefile sets
# CROSS_COMPILE and includes stm32.mk (core flags and the CMSIS header location), so this
# fragment does both too. The port directory is for boards whose mpconfigboard.h includes
# another board's.
CROSS_COMPILE ?= arm-none-eabi-
include stm32.mk
MBOOT_CPP_FLAGS = $(CFLAGS_MCU_$(MCU_SERIES)) -D$(CMSIS_MCU) -I$(TOP)/lib/CMSIS_6/CMSIS/Core/Include -I$(STM32LIB_CMSIS_ABS)/Include -I$(TOP)/ports/stm32
include $(TOP)/shared/mboot/mboot_common.mk

# MICROPY_HW_MBOOT_APP selects the port hooks: the flash ECC NMI handler, the reset flags
# handoff and the routing of machine.bootloader().

# The firmware starts after the image header in the primary slot. The MCU's linker script
# takes the addresses from the layout, rather than the legacy STM32 Mboot layout.
override USE_MBOOT = 0
MBOOT_SRAM_LD = $(BUILD)/mboot_sram.ld
MBOOT_CMSIS_H = stm32$(MCU_SERIES)xx.h
LD_FILES := $(MBOOT_LD_LAYOUT) $(MBOOT_SRAM_LD) $(patsubst %.ld,%_mboot.ld,$(firstword $(LD_FILES))) boards/common_bl.ld
TEXT0_ADDR := $(MBOOT_APP_LINK_ADDR)

# The include directories of the crypto libraries of bootutil come last (mboot_app_rules.mk):
# NimBLE and Mbed TLS of the application have headers of the same names that the application
# sources have to find first.
MBOOT_CRYPTO_INC = $(filter %/ext/tinycrypt/lib/include %/ext/mbedtls-asn1/include,$(MBOOT_INC))
INC += $(filter-out $(MBOOT_CRYPTO_INC),$(MBOOT_INC))
CFLAGS += $(MBOOT_CFLAGS) -DMICROPY_HW_MBOOT_APP=1

# The port file shared with the bootloader: the backup words, the flash ECC flags and the
# controller primitives under the flash policy of shared/mboot.
SRC_C += mboot_port.c

LIB_SRC_C += $(MBOOT_SRC_C)
OBJ += $(MBOOT_GEN_SRC_C:.c=.o)

# Bootutil is third party code; its warnings do not fail the build.
$(BUILD)/lib/mcuboot/%.o: CFLAGS += -Wno-error
