# mboot_bootloader.mk - build rules of an Mboot bootloader (MBOOT_ROLE=bootloader) on a
# GNU toolchain, included by the port's bootloader Makefile after it has set up the port:
#
#   include ../../py/mkenv.mk, the board's mpconfigboard.mk and the port's toolchain settings
#   MBOOT_ROLE = bootloader, MBOOT_DEV_DIR, MBOOT_CPP_FLAGS and
#   include $(TOP)/shared/mboot/mboot_common.mk
#   INC        include paths, with $(MBOOT_INC)
#   CFLAGS     the options of the MCU and of the board, with $(MBOOT_CFLAGS)
#   MBOOT_SRAM_LD       output of the SRAM linker symbols, see mboot_sram.mk
#   MBOOT_CMSIS_H       the CMSIS device header that mboot_sram.ld.S of the port is preprocessed with
#   SRC_C, SRC_TINYUSB_C, SRC_HAL, SRC_O   sources relative to the port directory or TOP, with
#                       $(MBOOT_SRC_C), and for the DFU front end $(MBOOT_TINYUSB_SRC_C)
#   LIBS       the libraries to link (libgcc is added here)
#
# The bootloader is linked with the layout (mboot_layout.ld, from the board's mpconfigboard.h),
# the SRAM symbols and mboot_bootloader.ld. Targets: firmware.{elf,bin,hex} in $(BUILD), and
# deploy-bootloader PROBE=<serial> PYOCD_TARGET=<pyocd target>, which programs it with pyocd.

CFLAGS += -Wall -Wpointer-arith -Wdouble-promotion -Wfloat-conversion -Werror -std=gnu99 $(CFLAGS_EXTRA)
CFLAGS += $(INC)
# Remove uncalled code from the final image.
CFLAGS += -fdata-sections -ffunction-sections
CFLAGS += -g

ifeq ($(DEBUG),1)
COPT = -Og
else
# -O3 does not build tinycrypt (uECC_vli_mult).
COPT ?= -Os -DNDEBUG
endif
CFLAGS += $(COPT)

# Standard C functions like memset need to be compiled with special flags so the compiler
# does not optimise these functions in terms of themselves.
CFLAGS_BUILTIN ?= -ffreestanding -fno-builtin -fno-lto
$(BUILD)/shared/libc/string0.o: CFLAGS += $(CFLAGS_BUILTIN)

# Bootutil and tinycrypt are third party code; their warnings do not fail the build.
$(BUILD)/lib/mcuboot/%.o: CFLAGS += -Wno-error

MBOOT_BOOT_LINKER_SCRIPT ?= $(TOP)/shared/mboot/mboot_bootloader.ld
LDFLAGS += -nostdlib -L . -T$(MBOOT_LD_LAYOUT) -T$(MBOOT_SRAM_LD) -T$(MBOOT_BOOT_LINKER_SCRIPT) -Map=$(BUILD)/firmware.map --cref --gc-sections --no-warn-rwx-segments
LIBS += $(shell $(CC) $(CFLAGS) -print-libgcc-file-name)

OBJ += $(addprefix $(BUILD)/, $(SRC_O))
OBJ += $(addprefix $(BUILD)/, $(SRC_C:.c=.o))
OBJ += $(addprefix $(BUILD)/, $(SRC_TINYUSB_C:.c=.o))
OBJ += $(addprefix $(BUILD)/, $(SRC_HAL:.c=.o))
OBJ += $(MBOOT_GEN_SRC_C:.c=.o)

all: $(BUILD)/firmware.bin $(BUILD)/firmware.hex

include $(TOP)/shared/mboot/mboot_sram.mk

$(BUILD)/firmware.elf: $(OBJ) $(MBOOT_BOOT_LINKER_SCRIPT) $(MBOOT_SRAM_LD) $(MBOOT_LD_LAYOUT)
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

# The key table and layout rules.
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
