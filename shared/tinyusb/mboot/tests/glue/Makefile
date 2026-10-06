# Host tests of shared/mcuboot/src/dfu_glue.c on an in-memory MCUboot environment (fake_mcuboot.c).
#
#   make test        run the glue tests for each slot policy (swap using offset, overwrite-external, single)
#   make test-board  build and run the glue tests and the write align mismatch test for BOARD
#   make clean
#
# The configuration is the NUCLEO-H563ZI board header and STM32H5 device description from
# ports/stm32/mcuboot, via shared/mcuboot/mcuboot_common.mk, so the device table, DFU regions
# and write ranges are the same as the real build. BOARD selects the slot policy: h5 (swap),
# glue_ow (overwrite-external) or glue_single (see tests/mcuboot/host/boards.mk).
# TinyUSB is stubbed by the headers in stubs/.

TOP := $(abspath ../../../../..)
BOARD ?= h5
BUILD ?= build/$(BOARD)

include $(TOP)/tests/mcuboot/host/boards.mk

MCUBOOT_ROLE := bootloader
MCUBOOT_GEN_DIR := $(abspath $(BUILD))/gen
include $(TOP)/shared/mcuboot/mcuboot_common.mk

MBOOT := $(TOP)/shared/tinyusb/mboot
TESTS := $(TOP)/shared/tinyusb/mboot/tests
OUT := $(abspath $(BUILD))

CFLAGS := -Wall -Werror -std=gnu99 -ffreestanding -fno-builtin -g -O1 -MMD -MP \
	-I$(MBOOT)/include -I$(TESTS) -DMBOOT_TESTS_FAKE_TUSB=1
GLUE_CFLAGS := $(CFLAGS) -I$(CURDIR)/stubs -I$(CURDIR) $(MCUBOOT_INC) $(MCUBOOT_CPP_FLAGS) $(MCUBOOT_CFLAGS)

SRC := $(MBOOT)/common/mboot_dfu.c $(MBOOT)/common/mboot_region.c $(MBOOT)/common/mboot_usbd.c \
	$(TOP)/shared/mcuboot/src/dfu_glue.c $(TOP)/shared/mcuboot/src/flash_map.c \
	$(TESTS)/fake_tusb.c $(CURDIR)/fake_mcuboot.c
vpath %.c $(sort $(dir $(SRC)))

OBJ := $(addprefix $(OUT)/obj/,$(notdir $(SRC:.c=.o)))
-include $(OBJ:.o=.d) $(OUT)/obj/dfu_regions.d $(OUT)/obj/test_glue.d $(OUT)/obj/test_glue_align_mismatch.d

.PHONY: test test-board clean

test:
	$(MAKE) BOARD=h5 test-board
	$(MAKE) BOARD=glue_ow test-board
	$(MAKE) BOARD=glue_single test-board

test-board: $(OUT)/test_glue $(OUT)/test_glue_align_mismatch
	$(OUT)/test_glue
	$(OUT)/test_glue_align_mismatch

$(OUT)/obj:
	mkdir -p $@

$(OBJ): $(OUT)/obj/%.o: %.c | $(OUT)/obj
	$(CC) $(GLUE_CFLAGS) -c -o $@ $<

# dfu_regions.c with its tables renamed, so the tests can replace the copy the glue sees.
$(OUT)/obj/dfu_regions.o: $(TOP)/shared/mcuboot/src/dfu_regions.c | $(OUT)/obj
	$(CC) $(GLUE_CFLAGS) -Dmcuboot_write_ranges=fk_real_write_ranges -Dmboot_port_get_regions=fk_real_get_regions -c -o $@ $<

$(OUT)/obj/test_glue.o: $(CURDIR)/test_glue.c | $(OUT)/obj
	$(CC) $(GLUE_CFLAGS) -c -o $@ $<

$(OUT)/obj/test_glue_align_mismatch.o: $(CURDIR)/test_glue_align_mismatch.c | $(OUT)/obj
	$(CC) $(GLUE_CFLAGS) -c -o $@ $<

$(OUT)/test_glue: $(OBJ) $(OUT)/obj/dfu_regions.o $(OUT)/obj/test_glue.o
	$(CC) -o $@ $^

# The core built with the default write align (4), the glue with the layout's.
$(OUT)/obj/mboot_dfu_align4.o: $(MBOOT)/common/mboot_dfu.c | $(OUT)/obj
	$(CC) $(CFLAGS) -c -o $@ $<

$(OUT)/test_glue_align_mismatch: $(filter-out $(OUT)/obj/mboot_dfu.o,$(OBJ)) $(OUT)/obj/dfu_regions.o \
		$(OUT)/obj/mboot_dfu_align4.o $(OUT)/obj/test_glue_align_mismatch.o
	$(CC) -o $@ $^

clean:
	rm -rf build
