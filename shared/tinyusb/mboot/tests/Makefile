CC ?= gcc
CFLAGS = -Wall -Werror -std=gnu99 -ffreestanding -fno-builtin -g -O1 \
         -I ../include -I .

SRCS_COMMON = ../common/mboot_dfu.c ../common/mboot_region.c \
              ../common/mboot_elem.c
SRCS_SUPPORT = fake_flash.c fake_transport.c test_regions_config.c
SRCS_TESTS = test_main.c test_dfu.c test_region.c test_elem.c test_vendor.c test_hooks.c

# mboot_usbd.c is built with MBOOT_TESTS_FAKE_TUSB=1 so it picks up the
# fake_tusb.h stubs instead of the TinyUSB headers. No submodule needed.
SRCS_USBD = ../common/mboot_usbd.c fake_tusb.c test_usbd.c

test_runner: $(SRCS_COMMON) $(SRCS_SUPPORT) $(SRCS_TESTS) $(SRCS_USBD)
	$(CC) $(CFLAGS) -DMBOOT_TESTS_FAKE_TUSB=1 -o $@ $^

# Write unit of 16 bytes (MBOOT_DFU_WRITE_ALIGN) instead of the default 4.
test_runner_align: ../common/mboot_dfu.c ../common/mboot_region.c fake_flash.c \
                   fake_transport.c test_regions_config.c test_align.c
	$(CC) $(CFLAGS) -DMBOOT_DFU_WRITE_ALIGN=16 -o $@ $^

# The core on its own: nothing here needs shared/mcuboot.
check_core: test_runner test_runner_align
	./test_runner
	./test_runner_align
	python3 test_pydfu_wire.py

# The MCUboot DFU glue (shared/mcuboot/src/dfu_glue.c) on top of the core, in glue/Makefile.
check_glue:
	$(MAKE) -C glue test

check: check_core check_glue

clean:
	rm -f test_runner test_runner_align
	$(MAKE) -C glue clean

.PHONY: check check_core check_glue clean
