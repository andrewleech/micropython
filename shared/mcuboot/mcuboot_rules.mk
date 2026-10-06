# Rules for MCUboot builds: the generated key table, regeneration of the layout files and, for
# the app role, image signing and packaging.
#
# Include this file from the port Makefile after mcuboot_common.mk, after $(OBJ) is complete
# (it lists $(MCUBOOT_GEN_SRC_C:.c=.o)), and after the rule that makes $(BUILD)/firmware.bin
# (app role) has been defined.
# Variables used (set by the including Makefile or mcuboot_common.mk): TOP, BUILD, PYTHON, ECHO,
# Q, MKDIR, OBJ, compile_c (the recipe that compiles $< to $@), MCUBOOT_ROLE, BOARD_DIR,
# MCUBOOT_DEV_DIR, MCUBOOT_GEN_DIR, MCUBOOT_GEN_CMD, MCUBOOT_PUBKEY, MCUBOOT_SIGN_KEY,
# MCUBOOT_PRODUCTION.
#
# Targets of the app role:
#   sign         $(BUILD)/firmware.signed.bin, for DFU, fsload and mcuboot.Writer
#   initial      $(BUILD)/firmware.initial.bin and .hex, for the first
#                programming of the primary slot with a programmer
#   dfu          $(BUILD)/firmware.signed.dfu, for tools/pydfu.py and dfu-util
#   sign-digest  $(BUILD)/firmware.digest, the SHA-256 to be signed externally
#   sign-apply   $(BUILD)/firmware.signed.bin from SIG=<signature file> made
#                externally for that digest (MCUBOOT_SIGN_PUBKEY=<pub.pem>
#                selects another public key than MCUBOOT_PUBKEY)
# "all" also builds firmware.signed.bin when MCUBOOT_SIGN_KEY is set.

.PHONY: sign initial dfu sign-digest sign-apply

# The generated key table is in the build directory already.
$(BUILD)/mcuboot_gen/%.o: $(MCUBOOT_GEN_DIR)/%.c
	$(call compile_c)

MCUBOOT_LAYOUT_JSON = $(MCUBOOT_GEN_DIR)/mcuboot_layout.json
MCUBOOT_GEN_STAMP = $(MCUBOOT_GEN_DIR)/mcuboot_layout.stamp

# The configuration is read from headers; the layout files follow them.
MCUBOOT_GEN_DEPS = $(BOARD_DIR)/mpconfigboard.h $(MCUBOOT_DIR)/include/mcuboot_layout.h \
	$(MCUBOOT_DEV_DIR)/mcuboot_dev.h $(TOP)/tools/mcuboot_gen.py $(MCUBOOT_PUBKEY)

MCUBOOT_GEN_FILES = mcuboot_layout.ld mcuboot_layout.mk mcuboot_layout.json mcuboot_keys_gen.c

# The generator writes all files of MCUBOOT_GEN_FILES in one run.
$(MCUBOOT_GEN_STAMP): $(MCUBOOT_GEN_DEPS)
	$(ECHO) "GEN mcuboot layout"
	$(Q)$(MKDIR) -p $(MCUBOOT_GEN_DIR)
	$(Q)$(MCUBOOT_GEN_CMD)
	$(Q)touch $@

$(addprefix $(MCUBOOT_GEN_DIR)/,$(MCUBOOT_GEN_FILES)): $(MCUBOOT_GEN_STAMP)

ifeq ($(MCUBOOT_ROLE),app)

MCUBOOT_SIGN_PY = $(PYTHON) $(TOP)/tools/mcuboot_sign.py
MCUBOOT_SIGN_FLAGS = --layout $(MCUBOOT_LAYOUT_JSON) \
	$(if $(filter 1,$(MCUBOOT_PRODUCTION)),--production)
MCUBOOT_SIGN_DEPS = $(BUILD)/firmware.bin $(MCUBOOT_LAYOUT_JSON) $(TOP)/tools/mcuboot_sign.py
MCUBOOT_REQUIRE_KEY = $(if $(MCUBOOT_SIGN_KEY),,$(error MCUBOOT_SIGN_KEY is not set))

sign: $(BUILD)/firmware.signed.bin
initial: $(BUILD)/firmware.initial.bin $(BUILD)/firmware.initial.hex
dfu: $(BUILD)/firmware.signed.dfu
sign-digest: $(BUILD)/firmware.digest

ifneq ($(MCUBOOT_SIGN_KEY),)
all: $(BUILD)/firmware.signed.bin
endif

$(BUILD)/firmware.signed.bin: $(MCUBOOT_SIGN_DEPS) $(MCUBOOT_SIGN_KEY)
	$(MCUBOOT_REQUIRE_KEY)
	$(ECHO) "MCUBOOT SIGN $@"
	$(Q)$(MCUBOOT_SIGN_PY) sign $(MCUBOOT_SIGN_FLAGS) -k $(MCUBOOT_SIGN_KEY) $< $@

# One run writes both files, signed once, so they carry the same signature.
$(BUILD)/firmware.initial.hex: $(MCUBOOT_SIGN_DEPS) $(MCUBOOT_SIGN_KEY)
	$(MCUBOOT_REQUIRE_KEY)
	$(ECHO) "MCUBOOT INITIAL $@ $(BUILD)/firmware.initial.bin"
	$(Q)$(MCUBOOT_SIGN_PY) initial $(MCUBOOT_SIGN_FLAGS) -k $(MCUBOOT_SIGN_KEY) \
		--hex $@ $< $(BUILD)/firmware.initial.bin

$(BUILD)/firmware.initial.bin: $(BUILD)/firmware.initial.hex

$(BUILD)/firmware.signed.dfu: $(BUILD)/firmware.signed.bin $(MCUBOOT_LAYOUT_JSON)
	$(ECHO) "MCUBOOT DFU $@"
	$(Q)$(MCUBOOT_SIGN_PY) dfu --layout $(MCUBOOT_LAYOUT_JSON) $< $@

$(BUILD)/firmware.digest: $(MCUBOOT_SIGN_DEPS)
	$(ECHO) "MCUBOOT DIGEST $@"
	$(Q)$(MCUBOOT_SIGN_PY) sign $(MCUBOOT_SIGN_FLAGS) --external digest $< $@

sign-apply: $(MCUBOOT_SIGN_DEPS)
	$(if $(SIG),,$(error SIG=<signature file> is not set))
	$(ECHO) "MCUBOOT SIGN-APPLY $(BUILD)/firmware.signed.bin"
	$(Q)$(MCUBOOT_SIGN_PY) sign $(MCUBOOT_SIGN_FLAGS) --external apply --sig $(SIG) \
		$(if $(MCUBOOT_SIGN_PUBKEY),--pubkey $(MCUBOOT_SIGN_PUBKEY)) $< \
		$(BUILD)/firmware.signed.bin

endif
