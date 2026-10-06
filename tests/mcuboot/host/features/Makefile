# Test of the optional port hooks of shared/mcuboot: the security counter backend of the port
# (security_cnt.c over mcuboot_port_seccnt_*) and the entropy source of the high FIH profile
# (fih_delay_rng.c over mcuboot_port_entropy_u8), on the host board portcnt.
#
#   make test

TOP := $(abspath ../../../..)
BUILD ?= build
BOARD ?= portcnt
PYTHON ?= python3

include $(TOP)/tests/mcuboot/host/boards.mk

MCUBOOT_ROLE := bootloader
MCUBOOT_GEN_DIR := $(abspath $(BUILD))/$(BOARD)/gen
include $(TOP)/shared/mcuboot/mcuboot_common.mk

OUT := $(abspath $(BUILD))/$(BOARD)

# The sources under test: the counter glue and the entropy glue, which the layout selects, and
# the fault injection hardening runtime of bootutil.
SRC_DIR := $(TOP)/$(MCUBOOT_SRC_DIR)
OBJ := $(addprefix $(OUT)/,feature_test.o security_cnt.o fih_delay_rng.o fault_injection_hardening.o)

CFLAGS := -std=gnu99 -O1 -g -Wall -Wextra -Wundef -Werror -Wno-unused-parameter -Wno-ignored-qualifiers $(MCUBOOT_INC) $(MCUBOOT_CPP_FLAGS) $(MCUBOOT_CFLAGS)

vpath %.c $(CURDIR) $(SRC_DIR) $(TOP)/$(MCUBOOT_BU_DIR)

.PHONY: test clean
test: $(OUT)/feature_test
	$<

$(OUT)/feature_test: $(OBJ)
	$(CC) -o $@ $^

$(OUT)/%.o: %.c $(MCUBOOT_LD_LAYOUT) | $(OUT)
	$(CC) $(CFLAGS) -c -o $@ $<

# The failure loop in this file uses the Arm mnemonic "b", so build it without warnings.
$(OUT)/fault_injection_hardening.o: CFLAGS += -w -include $(TOP)/tests/mcuboot/host/core/core_fih_host.h

$(OUT):
	mkdir -p $@

clean:
	rm -rf $(BUILD)
