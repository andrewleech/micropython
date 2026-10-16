# Application-side Mboot integration for the explicitly selected RT1052/RT1176 M7 boards.
ifeq ($(MBOOT),1)
ifneq ($(filter $(BOARD),SEEED_ARCH_MIX MIMXRT1170_EVK),$(BOARD))
$(error Mboot is not configured for board $(BOARD))
endif

ifeq ($(MBOOT_ROMFS),)
$(error MBOOT_ROMFS=<ROMFS image> is required for signed Mboot builds)
endif

MBOOT_ROLE = app
MBOOT_DEV_DIR = $(CURDIR)
MBOOT_SRAM_LD = $(BUILD)/mboot_sram.ld
MBOOT_CMSIS_H = $(MCU_SERIES)$(MCU_CORE).h
MBOOT_CPP_FLAGS = -DCPU_$(MCU_VARIANT)$(MCU_CORE) -DMICROPY_HW_ENABLE_MBOOT=1 -DMICROPY_HW_FLASH_SIZE=$(MICROPY_HW_FLASH_SIZE)
include $(TOP)/shared/mboot/mboot_common.mk
MBOOT_MCU_SRC_C := $(addprefix shared/mboot/src/, backup_words.c fault.c)
include $(TOP)/shared/mboot/mboot_sram.mk

# The layout generator's bound already excludes the image header, TLV allowance and swap trailer.
# ROMFS is linked at the end of that signed payload, so its full reserved size is authenticated.
LD_FILES := $(MBOOT_LD_LAYOUT) $(MBOOT_SRAM_LD) boards/$(MCU_SERIES).ld boards/common.ld

CFLAGS += $(MBOOT_CFLAGS) -DMICROPY_HW_ENABLE_MBOOT=1 -DMICROPY_HW_MBOOT_APP=1 -DMICROPY_HW_QSPI_NOR_RUNTIME_CONFIG=1
LDDEFINES += -DMICROPY_HW_ENABLE_MBOOT=1
INC += $(MBOOT_INC)
SRC_C += mboot_port.c $(MBOOT_SRC_C) $(MBOOT_MCU_SRC_C)

MBOOT_ROMFS_BYTES = $(shell printf '%d' $(MICROPY_HW_ROMFS_BYTES))
MBOOT_ROMFS_PADDED = $(BUILD)/mboot_romfs.bin
MBOOT_ROMFS_OBJ = $(BUILD)/mboot_romfs.o
OBJ += $(MBOOT_ROMFS_OBJ)

$(MBOOT_ROMFS_PADDED): $(MBOOT_ROMFS)
	$(Q)test "$$(od -An -tx1 -N3 "$<" | tr -d ' \n')" = d2cd31
	$(Q)test "$$(stat -c %s "$<")" -le "$(MBOOT_ROMFS_BYTES)"
	$(Q)cp "$<" "$@"
	$(Q)truncate -s $(MBOOT_ROMFS_BYTES) "$@"

$(MBOOT_ROMFS_OBJ): $(MBOOT_ROMFS_PADDED)
	$(ECHO) "OBJCOPY signed ROMFS $<"
	$(Q)$(OBJCOPY) -I binary -O elf32-littlearm -B arm --rename-section .data=.mboot_romfs,alloc,load,readonly,data,contents $< $@

$(BUILD)/firmware.elf: $(MBOOT_LAYOUT_JSON) $(MBOOT_SRAM_LD)

include $(TOP)/shared/mboot/mboot_rules.mk
endif
