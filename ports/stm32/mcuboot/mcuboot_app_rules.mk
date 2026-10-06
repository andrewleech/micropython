# Rules of the MCUboot application variant of the stm32 port (MCUBOOT=1), included at the end
# of the Makefile. See mcuboot_app.mk.

# The crypto headers of bootutil, after the include directories of the application.
CFLAGS += $(MCUBOOT_CRYPTO_INC)

# The key table and layout rules, and the sign, initial and dfu targets.
include $(TOP)/shared/mcuboot/mcuboot_rules.mk

# The SRAM linker symbols; firmware.elf lists its prerequisites as link inputs, so the script is
# order-only.
include $(MCUBOOT_DEV_DIR)/mcuboot_sram.mk
$(BUILD)/firmware.elf: | $(MCUBOOT_SRAM_LD)

# Programming: the debug probe is always named by serial number, there is no default. The
# pyocd target comes from PYOCD_TARGET in the board's mpconfigboard.mk. Only the sectors of the
# image are erased and the option bytes are left alone.
PYOCD ?= pyocd

# The padded binary covers the whole primary slot, trailer included, and is programmed in one
# run. The sparse Intel HEX of the same image is not used: pyocd reports the trailer segment
# (about 115 KiB of erased flash away from the image) as programmed but leaves the trailer words
# erased, so the image would boot without its magic and image_ok flag.
.PHONY: deploy-initial
deploy-initial: $(BUILD)/firmware.initial.bin
	$(if $(PROBE),,$(error PROBE=<serial number of the debug probe> is required))
	$(if $(PYOCD_TARGET),,$(error PYOCD_TARGET=<pyocd target of the board> is required))
	$(ECHO) "Writing $< to the board with probe $(PROBE)"
	$(Q)$(PYOCD) flash --erase sector -u $(PROBE) -t $(PYOCD_TARGET) -a $(MCUBOOT_PRIMARY_ADDR) $<
