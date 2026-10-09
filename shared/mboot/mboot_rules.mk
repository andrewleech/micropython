# Rules for Mboot builds: the generated key table, regeneration of the layout files and, for
# the app role, image signing and packaging.
#
# Include this file from the port Makefile after mboot_common.mk, after $(OBJ) is complete
# (it lists $(MBOOT_GEN_SRC_C:.c=.o)), and after the rule that makes $(BUILD)/firmware.bin
# (app role) has been defined.
# Variables used (set by the including Makefile or mboot_common.mk): TOP, BUILD, PYTHON, ECHO,
# Q, MKDIR, OBJ, compile_c (the recipe that compiles $< to $@), MBOOT_ROLE, BOARD_DIR,
# MBOOT_DEV_DIR, MBOOT_GEN_DIR, MBOOT_GEN_CMD, MBOOT_PUBKEY, MBOOT_SIGN_KEY,
# MBOOT_PRODUCTION.
#
# Targets of the app role:
#   sign         $(BUILD)/firmware.signed.bin, for DFU and fsload
#   initial      $(BUILD)/firmware.initial.bin and .hex, for the first
#                programming of the primary slot with a programmer
#   dfu          $(BUILD)/firmware.signed.dfu, for tools/pydfu.py and dfu-util
#   sign-digest  $(BUILD)/firmware.digest, the SHA-256 to be signed externally
#   sign-apply   $(BUILD)/firmware.signed.bin from SIG=<signature file> made
#                externally for that digest (MBOOT_SIGN_PUBKEY=<pub.pem>
#                selects another public key than MBOOT_PUBKEY)
# "all" also builds firmware.signed.bin when MBOOT_SIGN_KEY is set.

.PHONY: sign initial dfu sign-digest sign-apply

# The generated key table is in the build directory already.
$(BUILD)/mboot_gen/%.o: $(MBOOT_GEN_DIR)/%.c
	$(call compile_c)

MBOOT_LAYOUT_JSON = $(MBOOT_GEN_DIR)/mboot_layout.json
MBOOT_GEN_STAMP = $(MBOOT_GEN_DIR)/mboot_layout.stamp

# The configuration is read from headers; the layout files follow them.
MBOOT_GEN_DEPS = $(BOARD_DIR)/mpconfigboard.h $(MBOOT_DIR)/include/mboot_layout.h \
	$(MBOOT_DEV_DIR)/mboot_dev.h $(TOP)/tools/mboot_gen.py $(MBOOT_PUBKEY)

MBOOT_GEN_FILES = mboot_layout.ld mboot_layout.mk mboot_layout.json mboot_keys_gen.c

# The generator writes all files of MBOOT_GEN_FILES in one run.
$(MBOOT_GEN_STAMP): $(MBOOT_GEN_DEPS)
	$(ECHO) "GEN mboot layout"
	$(Q)$(MKDIR) -p $(MBOOT_GEN_DIR)
	$(Q)$(MBOOT_GEN_CMD)
	$(Q)touch $@

$(addprefix $(MBOOT_GEN_DIR)/,$(MBOOT_GEN_FILES)): $(MBOOT_GEN_STAMP)

ifeq ($(MBOOT_ROLE),app)

MBOOT_SIGN_PY = $(PYTHON) $(TOP)/tools/mboot_sign.py
MBOOT_SIGN_FLAGS = --layout $(MBOOT_LAYOUT_JSON) \
	$(if $(filter 1,$(MBOOT_PRODUCTION)),--production)
MBOOT_SIGN_DEPS = $(BUILD)/firmware.bin $(MBOOT_LAYOUT_JSON) $(TOP)/tools/mboot_sign.py
MBOOT_REQUIRE_KEY = $(if $(MBOOT_SIGN_KEY),,$(error MBOOT_SIGN_KEY is not set))

sign: $(BUILD)/firmware.signed.bin
initial: $(BUILD)/firmware.initial.bin $(BUILD)/firmware.initial.hex
dfu: $(BUILD)/firmware.signed.dfu
sign-digest: $(BUILD)/firmware.digest

ifneq ($(MBOOT_SIGN_KEY),)
all: $(BUILD)/firmware.signed.bin
endif

$(BUILD)/firmware.signed.bin: $(MBOOT_SIGN_DEPS) $(MBOOT_SIGN_KEY)
	$(MBOOT_REQUIRE_KEY)
	$(ECHO) "MBOOT SIGN $@"
	$(Q)$(MBOOT_SIGN_PY) sign $(MBOOT_SIGN_FLAGS) -k $(MBOOT_SIGN_KEY) $< $@

# One run writes both files, signed once, so they carry the same signature.
$(BUILD)/firmware.initial.hex: $(MBOOT_SIGN_DEPS) $(MBOOT_SIGN_KEY)
	$(MBOOT_REQUIRE_KEY)
	$(ECHO) "MBOOT INITIAL $@ $(BUILD)/firmware.initial.bin"
	$(Q)$(MBOOT_SIGN_PY) initial $(MBOOT_SIGN_FLAGS) -k $(MBOOT_SIGN_KEY) \
		--hex $@ $< $(BUILD)/firmware.initial.bin

$(BUILD)/firmware.initial.bin: $(BUILD)/firmware.initial.hex

$(BUILD)/firmware.signed.dfu: $(BUILD)/firmware.signed.bin $(MBOOT_LAYOUT_JSON)
	$(ECHO) "MBOOT DFU $@"
	$(Q)$(MBOOT_SIGN_PY) dfu --layout $(MBOOT_LAYOUT_JSON) $< $@

$(BUILD)/firmware.digest: $(MBOOT_SIGN_DEPS)
	$(ECHO) "MBOOT DIGEST $@"
	$(Q)$(MBOOT_SIGN_PY) sign $(MBOOT_SIGN_FLAGS) --external digest $< $@

sign-apply: $(MBOOT_SIGN_DEPS)
	$(if $(SIG),,$(error SIG=<signature file> is not set))
	$(ECHO) "MBOOT SIGN-APPLY $(BUILD)/firmware.signed.bin"
	$(Q)$(MBOOT_SIGN_PY) sign $(MBOOT_SIGN_FLAGS) --external apply --sig $(SIG) \
		$(if $(MBOOT_SIGN_PUBKEY),--pubkey $(MBOOT_SIGN_PUBKEY)) $< \
		$(BUILD)/firmware.signed.bin

endif
