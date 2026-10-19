# Rule for the SRAM linker symbols of the Mboot bootloader and application, included by the
# bootloader Makefile and mboot_app_rules.mk after the first rule. MBOOT_SRAM_LD is the
# output, MBOOT_DEV_DIR the directory of mboot_sram.ld.S. The script names the symbols with
# macros from the CMSIS device header, so the C preprocessor evaluates them (CFLAGS holds the
# include paths of the CMSIS header and mboot_request.h). The sed strips the integer suffixes
# (UL) from the CMSIS constants because ld does not accept them.

$(MBOOT_SRAM_LD): $(MBOOT_DEV_DIR)/mboot_sram.ld.S $(TOP)/shared/mboot/include/mboot_request.h
	$(ECHO) "GEN $@"
	$(Q)$(MKDIR) -p $(dir $@)
	$(Q)$(CPP) -P -E $(CFLAGS) -imacros $(MBOOT_CMSIS_H) -imacros mboot_request.h $< | $(SED) -e 's/\(0[xX][0-9a-fA-F]*\)[uUlL]*/\1/g' -e '/^[[:space:]]*$$/d' > $@
