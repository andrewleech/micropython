# Host build and tests of the bootloader main flow (shared/mboot/src/boot_main.c and
# validate.c) together with the real bootutil, tinycrypt, DFU front end (dfu_glue.c over the DFU
# core of shared/mboot/dfu, TinyUSB replaced by its test fake) and fsload, on the fake flash
# of tests/mboot/host.
#
#   make test                  all cases on the counter board (bl), the version board (bl_ver), the board
#                              with the secondary slot and the filesystem in a SPI NOR flash (bl_spi), the
#                              overwrite-external policy with the secondary slot in the SPI NOR flash (bl_ow),
#                              the single slot policy (bl_single) and the PYBD_SF6 as shipped (bl_f7)
#   make test-board            all functional cases on BOARD only
#   make sweep                 the power cut sweeps (swap, revert, DFU session, empty and damaged primary) on BOARD
#   make test-app APP=<image>  install the signed application of an stm32 MBOOT_BACKEND=mcuboot build into the simulated NUCLEO_H563ZI
#   make test SAN=1 BUILD=build/san   build with AddressSanitizer and UBSan
#
# The sweeps are controlled with SWEEP_ARGS (see bl_test.c); EXTRA_CFLAGS is added to every compile.
#
# A device of the flash map that is not memory mapped (the SPI flash of bl and bl_spi) is the chip model
# of tests/mboot/host/spi_nor behind the real drivers/memory/spiflash.c. SWEEP_ARGS=--nor-scatter
# leaves a whole page or sector of the chip undefined when a cut interrupts a command.

TOP := $(abspath ../../../..)
.DEFAULT_GOAL := all
BOARD ?= bl
BUILD ?= build
SWEEP_ARGS ?=
PYTHON ?= python3

# BOARD names the configuration headers (see tests/mboot/host/boards.mk).
include $(TOP)/tests/mboot/host/boards.mk
MBOOT_ROLE := bootloader
MBOOT_GEN_DIR := $(abspath $(BUILD))/$(BOARD)/gen
include $(TOP)/shared/mboot/mboot_common.mk

OUT := $(abspath $(BUILD))/$(BOARD)
HOST := $(TOP)/tests/mboot/host
MBOOT_TESTS := $(TOP)/shared/mboot/dfu/tests

# Everything the bootloader links, except the port and the flash: bootutil with its crypto, the
# glue, the key table, the main flow and the front ends.
LIB_SRC := $(abspath $(addprefix $(TOP)/,$(MBOOT_BOOTUTIL_SRC_C) $(MBOOT_CRYPTO_SRC_C)))
GLUE_SRC := $(abspath $(addprefix $(TOP)/,$(MBOOT_GLUE_SRC_C) $(MBOOT_FRONT_SRC_C)))
GEN_SRC := $(MBOOT_GEN_SRC_C)
# Device 1 of the flash map is the SPI NOR chip model of spi_nor/ behind the real
# drivers/memory/spiflash.c and the flash_spiflash.c backend of shared/mboot.
# flash_spiflash.c is part of MBOOT_GLUE_SRC_C when the layout has a device 1; port_flash.c links
# it for the boards without one as well.
HOST_SRC := $(CURDIR)/bl_test.c $(CURDIR)/bl_port.c $(CURDIR)/bl_host.c $(HOST)/fake_flash.c \
	$(addprefix $(HOST)/spi_nor/,spi_nor.c port_flash.c) $(TOP)/drivers/memory/spiflash.c
ifneq ($(MBOOT_SPIFLASH_ENABLE),1)
HOST_SRC += $(TOP)/$(MBOOT_SRC_DIR)/flash_spiflash.c
endif

WARN := -Wall -Wextra -Werror -Wno-unused-parameter
INC := -I$(CURDIR)/include -I$(HOST)/spi_nor/include -I$(TOP) -I$(HOST) -I$(MBOOT_TESTS) $(MBOOT_INC)
CFLAGS := -std=gnu99 -O1 -g $(WARN) $(INC) $(MBOOT_CPP_FLAGS) $(MBOOT_CFLAGS) -DMBOOT_TESTS_FAKE_TUSB=1 \
	-DFAKE_FLASH_NO_PORT=1 -ffunction-sections -fdata-sections -MMD -MP $(EXTRA_CFLAGS)
LIB_CFLAGS := -std=gnu99 -O1 -g -w $(INC) $(MBOOT_CPP_FLAGS) $(MBOOT_CFLAGS) -DMBOOT_TESTS_FAKE_TUSB=1 \
	-ffunction-sections -fdata-sections -MMD -MP $(EXTRA_CFLAGS)
LDFLAGS := -Wl,--gc-sections -Wl,--wrap=mboot_dfu_recovery_run
ifdef SAN
CFLAGS += -fsanitize=address,undefined -fno-omit-frame-pointer
LIB_CFLAGS += -fsanitize=address,undefined -fno-omit-frame-pointer
LDFLAGS += -fsanitize=address,undefined
endif

vpath %.c $(sort $(dir $(LIB_SRC) $(GLUE_SRC) $(GEN_SRC) $(HOST_SRC)))

LIB_OBJ := $(addprefix $(OUT)/obj/,$(notdir $(LIB_SRC:.c=.o)))
OBJ := $(addprefix $(OUT)/obj/,$(notdir $(GLUE_SRC:.c=.o) $(GEN_SRC:.c=.o) $(HOST_SRC:.c=.o)))

BIN := $(OUT)/bl_test

-include $(LIB_OBJ:.o=.d) $(OBJ:.o=.d)

.PHONY: all test test-board test-app sweep images clean
all: $(BIN) images

$(LIB_OBJ): $(OUT)/obj/%.o: %.c | $(OUT)/obj
	$(CC) $(LIB_CFLAGS) -c -o $@ $<

# bootutil's failure loop is written with the Arm mnemonic "b".
$(OUT)/obj/fault_injection_hardening.o: LIB_CFLAGS += -include $(HOST)/glue/fih_host.h

# lib/uzlib compares signed and unsigned values in its paranoid checks.
$(addprefix $(OUT)/obj/,adler32.o crc32.o header.o tinflate.o): CFLAGS += -Wno-sign-compare

$(OBJ): $(OUT)/obj/%.o: %.c | $(OUT)/obj
	$(CC) $(CFLAGS) -c -o $@ $<

# The fake TinyUSB of the DFU core tests. The directory also holds a fake_flash.c of its own, so
# it is not on the vpath.
$(OUT)/obj/fake_tusb.o: $(MBOOT_TESTS)/fake_tusb.c | $(OUT)/obj
	$(CC) $(CFLAGS) -c -o $@ $<

$(OUT)/obj:
	mkdir -p $@

$(BIN): $(LIB_OBJ) $(OBJ) $(OUT)/obj/fake_tusb.o
	$(CC) $(LDFLAGS) -o $@ $^

# Images. The payload is pseudo random so that hash and signature cover real data. v1 is the
# image of the primary slot (padded, confirmed), v2 and v3 are updates; the negative variants are
# made from v2 (security counter 2, version 2.0.0). v2_initial is v2 as the image of the primary
# slot, for the downgrade cases.
IMG_DIR := $(OUT)/images
LAYOUT_JSON := $(MBOOT_GEN_DIR)/mboot_layout.json
SIGN := $(PYTHON) $(TOP)/tools/mboot_sign.py
IMGTOOL := $(PYTHON) $(MBOOT_LIB_DIR)/scripts/imgtool.py

BIG_DIR := $(IMG_DIR)/big
FAT_DIR := $(IMG_DIR)/fat
# The FAT volume is the size of the SPI flash of the board; the oversize image has to fit it.
FAT_SIZE := 1048576
ifeq ($(BOARD),bl_f7)
FAT_SIZE := 2097152
endif
images: $(BIG_DIR)/v3.bin $(IMG_DIR)/v1_initial.bin $(IMG_DIR)/v2_initial.bin $(IMG_DIR)/v2.bin $(IMG_DIR)/v2_same_counter.bin $(IMG_DIR)/v3.bin $(IMG_DIR)/small.bin $(IMG_DIR)/variants/manifest.json $(FAT_DIR)/good.fat

$(IMG_DIR):
	mkdir -p $@

$(IMG_DIR)/payload.bin: | $(IMG_DIR)
	$(PYTHON) -c "import random,sys; random.seed(7); sys.stdout.buffer.write(bytes(random.getrandbits(8) for _ in range(20000)))" > $@

$(IMG_DIR)/v1_initial.bin: $(IMG_DIR)/payload.bin $(LAYOUT_JSON)
	$(SIGN) initial --layout $(LAYOUT_JSON) --version 1.0.0+0 --security-counter 1 $< $@

$(IMG_DIR)/v2_initial.bin: $(IMG_DIR)/payload.bin $(LAYOUT_JSON)
	$(SIGN) initial --layout $(LAYOUT_JSON) --version 2.0.0+0 --security-counter 2 $< $@

$(IMG_DIR)/v2.bin: $(IMG_DIR)/payload.bin $(LAYOUT_JSON)
	$(SIGN) sign --layout $(LAYOUT_JSON) --version 2.0.0+0 --security-counter 2 $< $@

# Version 2.0.0 with the security counter of v1: an update that leaves the old image acceptable to
# the rollback check after it has been swapped out.
$(IMG_DIR)/v2_same_counter.bin: $(IMG_DIR)/payload.bin $(LAYOUT_JSON)
	$(SIGN) sign --layout $(LAYOUT_JSON) --version 2.0.0+0 --security-counter 1 $< $@

$(IMG_DIR)/v3.bin: $(IMG_DIR)/payload.bin $(LAYOUT_JSON)
	$(SIGN) sign --layout $(LAYOUT_JSON) --version 3.0.0+0 --security-counter 3 $< $@

$(IMG_DIR)/variants/manifest.json: $(IMG_DIR)/v2.bin $(LAYOUT_JSON)
	$(PYTHON) $(TOP)/tests/mboot/make_variants.py --layout $(LAYOUT_JSON) $< $(IMG_DIR)/variants

# An update of 2000 bytes of payload, which is less than one erase unit of the boards with swap
# using offset (the update path refuses it there). tools/mboot_sign.py refuses to make it, so
# imgtool signs it with the arguments of the layout.
$(IMG_DIR)/small_payload.bin: | $(IMG_DIR)
	$(PYTHON) -c "import random,sys; random.seed(13); sys.stdout.buffer.write(bytes(random.getrandbits(8) for _ in range(2000)))" > $@

$(IMG_DIR)/small.bin: $(IMG_DIR)/small_payload.bin $(LAYOUT_JSON)
	$(IMGTOOL) sign $(MBOOT_IMGTOOL_SIGN_ARGS) --version 2.0.0+0 --security-counter 2 -k $(MBOOT_SIGN_KEY) $< $@

# The variants as FAT volumes with the image as /update.bin, for the fsload cases.
$(FAT_DIR)/good.fat: $(IMG_DIR)/variants/manifest.json $(IMG_DIR)/small.bin $(CURDIR)/mkfat.py
	$(PYTHON) $(CURDIR)/mkfat.py --size $(FAT_SIZE) $(FAT_DIR) $(wildcard $(IMG_DIR)/variants/*.bin) $(IMG_DIR)/small.bin

# The same images with a 120000 byte payload (a swap of 15 sectors).
$(BIG_DIR):
	mkdir -p $@

$(BIG_DIR)/payload.bin: | $(BIG_DIR)
	$(PYTHON) -c "import random,sys; random.seed(11); sys.stdout.buffer.write(bytes(random.getrandbits(8) for _ in range(120000)))" > $@

$(BIG_DIR)/v1_initial.bin: $(BIG_DIR)/payload.bin $(LAYOUT_JSON)
	$(SIGN) initial --layout $(LAYOUT_JSON) --version 1.0.0+0 --security-counter 1 $< $@

$(BIG_DIR)/v2.bin: $(BIG_DIR)/payload.bin $(LAYOUT_JSON)
	$(SIGN) sign --layout $(LAYOUT_JSON) --version 2.0.0+0 --security-counter 2 $< $@

$(BIG_DIR)/v3.bin: $(BIG_DIR)/payload.bin $(LAYOUT_JSON) $(BIG_DIR)/v1_initial.bin $(BIG_DIR)/v2.bin
	$(SIGN) sign --layout $(LAYOUT_JSON) --version 3.0.0+0 --security-counter 3 $< $@

test-board: $(BIN) images
	$(BIN) --images $(IMG_DIR) all $(SWEEP_ARGS)

# The sweeps (a few minutes with --big): SWEEP_ARGS="--stride 1 --big"
sweep: $(BIN) images
	$(BIN) --images $(IMG_DIR) sweep $(SWEEP_ARGS)

# The application image of the stm32 MBOOT_BACKEND=mcuboot build for NUCLEO_H563ZI installed into an empty
# device of the same layout (BOARD=h5 is that board): make test-app APP=<firmware.signed.bin>
test-app:
	$(MAKE) BOARD=h5 BUILD=$(BUILD) all
	$(abspath $(BUILD))/h5/bl_test --images $(abspath $(BUILD))/h5/images --app $(APP) real_app

test:
	$(MAKE) BOARD=bl BUILD=$(BUILD) test-board
	$(MAKE) BOARD=bl_ver BUILD=$(BUILD) test-board
	$(MAKE) BOARD=bl_spi BUILD=$(BUILD) test-board
	$(MAKE) BOARD=bl_ow BUILD=$(BUILD) test-board
	$(MAKE) BOARD=bl_single BUILD=$(BUILD) test-board
	$(MAKE) BOARD=bl_f7 BUILD=$(BUILD) test-board

clean:
	rm -rf $(BUILD)
