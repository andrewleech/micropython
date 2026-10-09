# Host tests of shared/mboot/src/dfu_glue.c on an in-memory Mboot environment (fake_mboot.c).
#
#   make test        run the glue tests for each slot policy (swap using offset, overwrite-external, single)
#   make test-board  build and run the glue tests and the write align mismatch test for BOARD
#   make clean
#
# The configuration is the NUCLEO-H563ZI board header and STM32H5 device description from
# ports/stm32/mboot/mcuboot, via shared/mboot/mboot_common.mk, so the device table, DFU regions
# and write ranges are the same as the real build. BOARD selects the slot policy: h5 (swap),
# glue_ow (overwrite-external) or glue_single (see tests/mboot/host/boards.mk).
# TinyUSB is stubbed by the headers in stubs/.

TOP := $(abspath ../../../../..)
BOARD ?= h5
BUILD ?= build/$(BOARD)

include $(TOP)/tests/mboot/host/boards.mk

MBOOT_ROLE := bootloader
MBOOT_GEN_DIR := $(abspath $(BUILD))/gen
include $(TOP)/shared/mboot/mboot_common.mk

MBOOT := $(TOP)/shared/mboot/dfu
TESTS := $(TOP)/shared/mboot/dfu/tests
OUT := $(abspath $(BUILD))

CFLAGS := -Wall -Werror -std=gnu99 -ffreestanding -fno-builtin -g -O1 -MMD -MP \
	-I$(MBOOT)/include -I$(TESTS) -DMBOOT_TESTS_FAKE_TUSB=1
GLUE_CFLAGS := $(CFLAGS) -I$(CURDIR)/stubs -I$(CURDIR) $(MBOOT_INC) $(MBOOT_CPP_FLAGS) $(MBOOT_CFLAGS)

SRC := $(MBOOT)/core/mboot_dfu.c $(MBOOT)/core/mboot_region.c $(MBOOT)/core/mboot_usbd.c \
	$(TOP)/shared/mboot/src/dfu_glue.c $(TOP)/shared/mboot/src/flash_map.c \
	$(TESTS)/fake_tusb.c $(CURDIR)/fake_mboot.c
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
$(OUT)/obj/dfu_regions.o: $(TOP)/shared/mboot/src/dfu_regions.c | $(OUT)/obj
	$(CC) $(GLUE_CFLAGS) -Dmboot_write_ranges=fk_real_write_ranges -Dmboot_port_get_regions=fk_real_get_regions -c -o $@ $<

$(OUT)/obj/test_glue.o: $(CURDIR)/test_glue.c | $(OUT)/obj
	$(CC) $(GLUE_CFLAGS) -c -o $@ $<

$(OUT)/obj/test_glue_align_mismatch.o: $(CURDIR)/test_glue_align_mismatch.c | $(OUT)/obj
	$(CC) $(GLUE_CFLAGS) -c -o $@ $<

$(OUT)/test_glue: $(OBJ) $(OUT)/obj/dfu_regions.o $(OUT)/obj/test_glue.o
	$(CC) -o $@ $^

# The core built with the default write align (4), the glue with the layout's.
$(OUT)/obj/mboot_dfu_align4.o: $(MBOOT)/core/mboot_dfu.c | $(OUT)/obj
	$(CC) $(CFLAGS) -c -o $@ $<

$(OUT)/test_glue_align_mismatch: $(filter-out $(OUT)/obj/mboot_dfu.o,$(OBJ)) $(OUT)/obj/dfu_regions.o \
		$(OUT)/obj/mboot_dfu_align4.o $(OUT)/obj/test_glue_align_mismatch.o
	$(CC) -o $@ $^

clean:
	rm -rf build
