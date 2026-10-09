# Host power-cut sweep harness for MCUboot.
#
# Builds the unmodified bootutil from lib/mcuboot with tests/mboot/host/fake_flash.c and one of
# two flash map backends into one executable per board, then runs the sweep driver over it.
#
#   GLUE=local   (default) the minimal flash map backend in glue/: no ECC read policy and no
#                shadow words, so ECC-invalid units reach bootutil as errors
#   GLUE=shared  the flash map backend, shadow words and security counter of shared/mboot; a device
#                that is not memory mapped (BOARD=spi: the secondary slot in a SPI NOR) is the chip
#                model of spi_nor/ behind drivers/memory/spiflash.c
#
# BOARD selects the configuration (see boards.mk); both backends take the flash layout from the
# JSON that tools/mboot_gen.py writes for it, through shared/mboot/mboot_common.mk.
#
#   make test                              sweep swap and revert on the NUCLEO-H563ZI (BOARD=h5)
#   make test BOARD=nrf                    1 MiB flash, 4 KiB sectors, 4 byte write unit (rt: 8 MiB)
#   make test GLUE=shared                  the same with the shared/mboot glue
#   make test BOARD=move GLUE=shared       swap using move (scratch, overwrite for overwrite-external
#                                          and single for the single slot policy also need GLUE=shared)
#   make test BOARD=f7 GLUE=shared         the PYBD_SF6 flash map (sectors of 32, 128 and 256 KiB) with an image of
#                                          two sectors; f7_offset and f7_scratch are the same map with the
#                                          other swap modes
#   make test SWEEP_ARGS="--sectors 3 --modes before,after"
#   make dry-run                           print the host_sweep commands only
#   make layout                            print the path of the layout JSON of BOARD
#   make unit                              unit tests of the Python tools

TOP := $(abspath ../../..)
include $(TOP)/tests/mboot/host/boards.mk

PYTHON ?= python3
BUILD ?= build
GLUE ?= local
# The sectors of the PYBD_SF6 flash map are 256 KiB: an image of two of them is the largest that the
# slots of f7_offset and f7_scratch take, and 3,15 sectors do not fit.
ifneq ($(filter f7 f7_offset f7_scratch,$(BOARD)),)
SWEEP_ARGS ?= --sectors 2
endif
ifeq ($(filter $(GLUE),local shared),)
$(error GLUE must be local or shared)
endif

OUT := $(abspath $(BUILD))/$(BOARD)-$(GLUE)

MBOOT_ROLE := bootloader
MBOOT_GEN_DIR := $(OUT)/gen
include $(TOP)/shared/mboot/mboot_common.mk

ifeq ($(GLUE),local)
ifneq ($(MBOOT_POLICY)-$(MBOOT_SWAP_MODE),swap-offset)
$(error GLUE=local is bootutil with swap using offset only, BOARD=$(BOARD) needs GLUE=shared)
endif
endif

LAYOUT_JSON := $(MBOOT_GEN_DIR)/mboot_layout.json
KEY := $(MBOOT_SIGN_KEY)
IMGTOOL := $(PYTHON) $(MBOOT_LIB_DIR)/scripts/imgtool.py
BIN := $(OUT)/host_sweep

LIB_SRC := $(abspath $(addprefix $(TOP)/,$(MBOOT_BOOTUTIL_SRC_C) $(MBOOT_CRYPTO_SRC_C)))
GEN_SRC := $(MBOOT_GEN_SRC_C)
HOST_SRC := $(CURDIR)/host_sweep.c $(CURDIR)/fake_flash.c

ifeq ($(GLUE),shared)
# The boot decision does not need the request handoff or the jump code.
GLUE_SRC := $(abspath $(addprefix $(TOP)/,$(filter-out %/request.c %/arm_jump.c,$(MBOOT_GLUE_SRC_C))))
HOST_SRC += $(CURDIR)/glue/shared_port.c
# A flash map device that is not memory mapped is a SPI NOR chip model (spi_nor/) behind the real
# drivers/memory/spiflash.c and the flash_spiflash.c backend; spi_nor/port_flash.c is the port.
# MBOOT_GLUE_SRC_C includes flash_spiflash.c when the layout has a device 1. port_flash.c needs it
# for the boards without one as well.
HOST_SRC += $(addprefix $(CURDIR)/spi_nor/,spi_nor.c port_flash.c) $(TOP)/drivers/memory/spiflash.c
ifneq ($(MBOOT_SPIFLASH_ENABLE),1)
HOST_SRC += $(TOP)/$(MBOOT_SRC_DIR)/flash_spiflash.c
endif
INC := -I$(CURDIR)/spi_nor/include -I$(TOP) $(MBOOT_INC)
GLUE_FLAGS := -DHOST_SHARED_GLUE=1 -DFAKE_FLASH_NO_PORT=1
else
# The bootutil configuration in glue/include needs two numbers from the layout.
GLUE_SRC :=
HOST_SRC += $(CURDIR)/glue/host_glue.c
INC := -I$(CURDIR)/glue/include $(MBOOT_INC)

$(OUT)/layout.mk: $(LAYOUT_JSON) layout.py
	$(PYTHON) layout.py --make $< > $@

include $(OUT)/layout.mk
GLUE_FLAGS := -DHOST_MAX_ALIGN=$(MAX_ALIGN) -DHOST_MAX_IMG_SECTORS=$(MAX_IMG_SECTORS)
endif

COMMON_CFLAGS := -std=gnu99 -O2 -g -ffunction-sections -fdata-sections -MMD -MP \
	$(INC) $(MBOOT_CFLAGS) $(MBOOT_CPP_FLAGS) -I$(CURDIR) $(GLUE_FLAGS) $(EXTRA_CFLAGS)
CFLAGS := $(COMMON_CFLAGS) -Wall -Wno-unused-function -Wno-unused-variable
LIB_CFLAGS := $(COMMON_CFLAGS) -w
LDFLAGS := -Wl,--gc-sections

vpath %.c $(sort $(dir $(LIB_SRC) $(GLUE_SRC) $(GEN_SRC) $(HOST_SRC)))
LIB_OBJ := $(addprefix $(OUT)/obj/,$(notdir $(LIB_SRC:.c=.o)))
OBJ := $(addprefix $(OUT)/obj/,$(notdir $(GLUE_SRC:.c=.o) $(GEN_SRC:.c=.o) $(HOST_SRC:.c=.o)))
ifeq ($(GLUE),local)
$(LIB_OBJ) $(OBJ): $(OUT)/layout.mk
endif

-include $(wildcard $(OUT)/obj/*.d)

.PHONY: all test dry-run layout unit clean
all: $(BIN)

$(LIB_OBJ): $(OUT)/obj/%.o: %.c | $(OUT)/obj
	$(CC) $(LIB_CFLAGS) -c -o $@ $<

# lib/uzlib compares signed and unsigned values in its sanity checks.
$(addprefix $(OUT)/obj/,adler32.o crc32.o header.o tinflate.o): CFLAGS += -Wno-sign-compare

$(OBJ): $(OUT)/obj/%.o: %.c | $(OUT)/obj
	$(CC) $(CFLAGS) -c -o $@ $<

$(OUT)/obj/fault_injection_hardening.o: LIB_CFLAGS += -include $(CURDIR)/glue/fih_host.h

$(OUT)/obj:
	mkdir -p $@

$(BIN): $(LIB_OBJ) $(OBJ)
	$(CC) $(LDFLAGS) -o $@ $^

test: $(BIN)
	$(PYTHON) sweep.py --layout $(LAYOUT_JSON) --binary $(BIN) --key $(KEY) --work $(OUT)/work $(SWEEP_ARGS)

dry-run: $(BIN)
	$(PYTHON) sweep.py --layout $(LAYOUT_JSON) --binary $(BIN) --key $(KEY) --work $(OUT)/work --dry-run $(SWEEP_ARGS)

layout:
	@echo $(LAYOUT_JSON)

unit:
	$(PYTHON) test_tools.py

clean:
	rm -rf $(BUILD)
