# Checks that the linker symbols of mboot_layout.ld reach a linked image, using a toy linker
# script and two ways of giving the symbols to the linker:
#
#   make test            both ways, then check the addresses of the linked image against the layout
#   make test BOARD=h5   the same for the NUCLEO-H563ZI configuration
#
# preprocessed   The linker scripts go through the C preprocessor, as in ports/mimxrt
#                ($(CC) -E -x c $(LDDEFINES) $(LD_FILES) into one link.ld). mboot_layout.ld only holds
#                plain assignments, so it is one more file of LD_FILES, in front of the port script,
#                and the port script uses the symbols in its MEMORY expressions like numbers.
# multiple -T    The scripts go to the linker as they are, as in ports/stm32 and ports/nrf
#                (-T layout.ld -T port.ld). A symbol assigned in the first script is visible in the MEMORY
#                expressions of the second.

TOP := $(abspath ../../../..)
BUILD ?= build
BOARD ?= plain
PYTHON ?= python3

include $(TOP)/tests/mboot/host/boards.mk

MBOOT_ROLE := bootloader
MBOOT_GEN_DIR := $(abspath $(BUILD))/$(BOARD)/gen
include $(TOP)/shared/mboot/mboot_common.mk

OUT := $(abspath $(BUILD))/$(BOARD)
LD ?= ld
LDFLAGS := -nostdlib -static --gc-sections -e toy_start

.PHONY: test clean
test: $(OUT)/toy_pre.elf $(OUT)/toy_multi.elf
	$(PYTHON) $(CURDIR)/check.py $(MBOOT_GEN_DIR)/mboot_layout.json $(OUT)/toy_pre.elf $(OUT)/toy_multi.elf

$(OUT)/toy.o: toy.c | $(OUT)
	$(CC) -std=gnu99 -ffreestanding -fno-pic -Os -Wall -Werror -ffunction-sections -c -o $@ $<

$(OUT):
	mkdir -p $@

# The preprocessed flow. LDDEFINES stands for the -D options a port passes to its scripts.
LDDEFINES :=
$(OUT)/link.ld: $(MBOOT_LD_LAYOUT) toy.ld | $(OUT)
	$(CC) -E -x c $(LDDEFINES) $(MBOOT_LD_LAYOUT) toy.ld | grep -v '^#' > $@

$(OUT)/toy_pre.elf: $(OUT)/toy.o $(OUT)/link.ld
	$(LD) -T$(OUT)/link.ld $(LDFLAGS) -o $@ $<

# The two script flow.
$(OUT)/toy_multi.elf: $(OUT)/toy.o $(MBOOT_LD_LAYOUT) toy.ld
	$(LD) -T$(MBOOT_LD_LAYOUT) -Ttoy.ld $(LDFLAGS) -o $@ $<

clean:
	rm -rf $(BUILD)
