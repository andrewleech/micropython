# Rule for the SRAM linker symbols of the MCUboot bootloader and application, included by the
# bootloader Makefile and mcuboot_app_rules.mk after the first rule. MCUBOOT_SRAM_LD is the
# output, MCUBOOT_DEV_DIR the directory of mcuboot_sram.ld.S. The script names the symbols with
# macros from the CMSIS device header, so the C preprocessor evaluates them (CFLAGS holds the
# include paths of the CMSIS header and mcuboot_request.h). The sed strips the integer suffixes
# (UL) from the CMSIS constants because ld does not accept them.

$(MCUBOOT_SRAM_LD): $(MCUBOOT_DEV_DIR)/mcuboot_sram.ld.S $(TOP)/shared/mcuboot/include/mcuboot_request.h
	$(ECHO) "GEN $@"
	$(Q)$(MKDIR) -p $(dir $@)
	$(Q)$(CPP) -P -E $(CFLAGS) -imacros $(MCUBOOT_CMSIS_H) -imacros mcuboot_request.h $< | $(SED) -e 's/\(0[xX][0-9a-fA-F]*\)[uUlL]*/\1/g' -e '/^[[:space:]]*$$/d' > $@
