# Host build and tests of the shared/mcuboot core glue (flash map backend with ECC shadow words,
# security counter, update log, request handoff, printf, crc) together with the unmodified bootutil
# and tinycrypt, on the fake flash of tests/mcuboot/host.
#
# The configuration is the board header pair selected by BOARD (tests/mcuboot/host/boards.mk).
#
#   make test                  all scenarios on the NUCLEO-H563ZI configuration (BOARD=h5)
#   make test BOARD=plain      the same without ECC on a 4 KiB / 8 byte device
#   make test BOARD=f7         the PYBD_SF6 as shipped: sectors of 32, 128 and 256 KiB, a 4 byte write unit, no ECC,
#                              single slot policy (own image sizes); f7_offset and f7_scratch are the same flash
#                              map with swap using offset and swap using scratch
#   make control               torn-word sweep against a backend without the ECC policy (BOARD=stock)
#   make test SAN=1 BUILD=build/san   build with AddressSanitizer and UBSan
#   make arm                   compile the glue and bootutil for a Cortex-M core (ARM_CPU=...)
#
# The sweep options are passed through SWEEP_ARGS (see core_test.c); EXTRA_CFLAGS is added to every compile.

TOP := $(abspath ../../../..)
BUILD ?= build
SWEEP_ARGS ?=
PYTHON ?= python3

include $(TOP)/tests/mcuboot/host/boards.mk

MCUBOOT_ROLE := bootloader
MCUBOOT_GEN_DIR := $(abspath $(BUILD))/$(BOARD)/gen
include $(TOP)/shared/mcuboot/mcuboot_common.mk

OUT := $(abspath $(BUILD))/$(BOARD)
IMGTOOL := $(PYTHON) $(MCUBOOT_LIB_DIR)/scripts/imgtool.py
KEY := $(MCUBOOT_PUBKEY)

LIB_SRC := $(abspath $(addprefix $(TOP)/,$(MCUBOOT_BOOTUTIL_SRC_C) $(MCUBOOT_CRYPTO_SRC_C)))
# Device 1 of a layout is plain memory here: the SPI flash backend is not built.
GLUE_SRC := $(abspath $(addprefix $(TOP)/,$(filter-out %/flash_spiflash.c,$(MCUBOOT_GLUE_SRC_C))))

# The single slot policy raises the security counter in validate.c after boot_go(); the file
# includes bootutil's private headers, so it is built like the library.
ifeq ($(MCUBOOT_POLICY),single)
LIB_SRC += $(TOP)/$(MCUBOOT_SRC_DIR)/validate.c
endif
GEN_SRC := $(MCUBOOT_GEN_SRC_C)
HOST_SRC := $(CURDIR)/core_test.c $(CURDIR)/core_port.c $(CURDIR)/core_unit.c $(TOP)/tests/mcuboot/host/fake_flash.c

WARN := -Wall -Wextra -Wundef -Werror -Wno-unused-parameter -Wno-ignored-qualifiers
CFLAGS := -std=gnu99 -O1 -g $(WARN) $(MCUBOOT_INC) $(MCUBOOT_CPP_FLAGS) $(MCUBOOT_CFLAGS) -DMCUBOOT_TEST_FI=1 \
	-I$(TOP)/tests/mcuboot/host -ffunction-sections -fdata-sections -MMD -MP $(EXTRA_CFLAGS)
LIB_CFLAGS := -std=gnu99 -O1 -g -w $(MCUBOOT_INC) $(MCUBOOT_CPP_FLAGS) $(MCUBOOT_CFLAGS) -DMCUBOOT_TEST_FI=1 -ffunction-sections -fdata-sections -MMD -MP $(EXTRA_CFLAGS)
LDFLAGS := -Wl,--gc-sections
ifdef SAN
CFLAGS += -fsanitize=address,undefined -fno-omit-frame-pointer
LIB_CFLAGS += -fsanitize=address,undefined -fno-omit-frame-pointer
LDFLAGS += -fsanitize=address,undefined
endif

vpath %.c $(sort $(dir $(LIB_SRC) $(GLUE_SRC) $(GEN_SRC) $(HOST_SRC)))

LIB_OBJ := $(addprefix $(OUT)/obj/,$(notdir $(LIB_SRC:.c=.o)))
OBJ := $(addprefix $(OUT)/obj/,$(notdir $(GLUE_SRC:.c=.o) $(GEN_SRC:.c=.o) $(HOST_SRC:.c=.o)))

BIN := $(OUT)/core_test

-include $(LIB_OBJ:.o=.d) $(OBJ:.o=.d)

.PHONY: all test control arm images clean
all: $(BIN) images

$(LIB_OBJ): $(OUT)/obj/%.o: %.c | $(OUT)/obj
	$(CC) $(LIB_CFLAGS) -c -o $@ $<

$(OUT)/obj/fault_injection_hardening.o: LIB_CFLAGS += -include $(CURDIR)/core_fih_host.h

$(OBJ): $(OUT)/obj/%.o: %.c | $(OUT)/obj
	$(CC) $(CFLAGS) -c -o $@ $<

$(OUT)/obj:
	mkdir -p $@

$(BIN): $(LIB_OBJ) $(OBJ)
	$(CC) $(LDFLAGS) -o $@ $^

# Images. Payloads are pseudo random so that the hash and the signature cover real data.
# Sizes are the payload bytes; the signed image adds the header and the TLVs. The names are the
# size tags of core_test (--sizes). The default sizes span more than one erase unit on the boards
# with erase units up to 8 KiB. An offset swap needs images of more than one erase unit: the
# update path refuses a smaller one (MCUBOOT_MIN_IMAGE_SIZE). The boards with 256 KiB erase units
# use images of two sectors, which also fit the 512 KiB largest image of f7_offset and f7_scratch.
IMG_DIR := $(OUT)/images
ifneq ($(filter f7 f7_offset f7_scratch,$(BOARD)),)
IMG_SIZES := 2u:300000 3u:450000
else
IMG_SIZES := 3s:20000 15s:120000
endif
comma := ,
space := $(subst ,, )
IMG_TAGS := $(subst $(space),$(comma),$(foreach s,$(IMG_SIZES),$(word 1,$(subst :, ,$(s)))))
IMG_FILES := $(foreach s,$(IMG_SIZES),$(IMG_DIR)/$(word 1,$(subst :, ,$(s)))_v1.bin $(IMG_DIR)/$(word 1,$(subst :, ,$(s)))_v2.bin $(IMG_DIR)/$(word 1,$(subst :, ,$(s)))_v3.bin $(IMG_DIR)/$(word 1,$(subst :, ,$(s)))_v0.bin)

images: $(IMG_FILES)

# v1 is the confirmed image of the primary slot (counter 0), v2 and v3 are updates (counters 1 and 2),
# v0 is an older image (version 0.0.0, counter 0) for the downgrade check.
# The generated sign arguments carry the layout id TLV; the version and counter are overridden
# by the later options. Each version has its own payload.
define IMG_RULE
$(IMG_DIR)/$(1)_v$(3)_raw.bin: | $(IMG_DIR)
	$(PYTHON) -c "import random,sys; random.seed(int(sys.argv[1])+int(sys.argv[2])); sys.stdout.buffer.write(bytes(random.getrandbits(8) for _ in range(int(sys.argv[1]))))" $(2) $(3) > $$@
endef
define IMG_SIGN
$(IMG_DIR)/$(1)_v$(2).bin: $(IMG_DIR)/$(1)_v$(2)_raw.bin $(MCUBOOT_GEN_DIR)/mcuboot_layout.mk
	$(IMGTOOL) sign $(3) --version $(2).0.0 --security-counter $(4) -k $(KEY) $$< $$@
endef
$(foreach s,$(IMG_SIZES),$(foreach v,0 1 2 3,$(eval $(call IMG_RULE,$(word 1,$(subst :, ,$(s))),$(word 2,$(subst :, ,$(s))),$(v)))))
$(foreach s,$(IMG_SIZES),$(eval $(call IMG_SIGN,$(word 1,$(subst :, ,$(s))),1,$$(MCUBOOT_IMGTOOL_INITIAL_ARGS),0)))
$(foreach s,$(IMG_SIZES),$(eval $(call IMG_SIGN,$(word 1,$(subst :, ,$(s))),2,$$(MCUBOOT_IMGTOOL_SIGN_ARGS),1)))
$(foreach s,$(IMG_SIZES),$(eval $(call IMG_SIGN,$(word 1,$(subst :, ,$(s))),3,$$(MCUBOOT_IMGTOOL_SIGN_ARGS),2)))
$(foreach s,$(IMG_SIZES),$(eval $(call IMG_SIGN,$(word 1,$(subst :, ,$(s))),0,$$(MCUBOOT_IMGTOOL_SIGN_ARGS),0)))

$(IMG_DIR):
	mkdir -p $@

test: $(BIN) images
	$(BIN) --images $(IMG_DIR) --sizes $(IMG_TAGS) all $(SWEEP_ARGS)

control:
	$(MAKE) BOARD=stock BUILD=$(BUILD) all
	$(abspath $(BUILD))/stock/core_test --images $(abspath $(BUILD))/stock/images --stock torn $(SWEEP_ARGS)

# Compiles the glue and bootutil for a Cortex-M core (no link: the port functions are not here)
# and prints the object sizes. ARM_CPU selects the core.
ARM_CPU ?= cortex-m33
ARM_CC ?= arm-none-eabi-gcc
ARM_CFLAGS := -mcpu=$(ARM_CPU) -mthumb -Os -std=gnu99 -ffunction-sections -fdata-sections -Wall -Wextra -Wundef -Werror \
	-Wno-unused-parameter $(MCUBOOT_INC) $(MCUBOOT_CPP_FLAGS) $(MCUBOOT_CFLAGS)
ARM_LIB_CFLAGS := -mcpu=$(ARM_CPU) -mthumb -Os -std=gnu99 -ffunction-sections -fdata-sections -w $(MCUBOOT_INC) $(MCUBOOT_CPP_FLAGS) $(MCUBOOT_CFLAGS)

arm:
	mkdir -p $(OUT)/arm
	for f in $(GLUE_SRC) $(GEN_SRC); do \
		$(ARM_CC) $(ARM_CFLAGS) -c -o $(OUT)/arm/$$(basename $$f .c).o $$f || exit 1; done
	for f in $(LIB_SRC); do \
		$(ARM_CC) $(ARM_LIB_CFLAGS) -c -o $(OUT)/arm/$$(basename $$f .c).o $$f || exit 1; done
	arm-none-eabi-size -t $(OUT)/arm/*.o

clean:
	rm -rf $(BUILD)
