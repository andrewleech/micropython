CC ?= gcc
CFLAGS = -Wall -Werror -std=gnu99 -ffreestanding -fno-builtin -g -O1 \
         -I ../include -I .

SRCS_COMMON = ../common/mboot_dfu.c ../common/mboot_region.c \
              ../common/mboot_elem.c
SRCS_SUPPORT = fake_flash.c fake_transport.c test_regions_config.c
SRCS_TESTS = test_main.c test_dfu.c test_region.c test_elem.c test_vendor.c

SRCS_PACK = fake_flash.c fake_transport.c test_regions_config.c \
            test_pack_hook.c

# mboot_usbd.c is compiled with MBOOT_TESTS_FAKE_TUSB=1 so it uses fake_tusb.h
# stubs instead of real TinyUSB headers.  This avoids a dependency on the
# TinyUSB submodule in the host test build.
SRCS_USBD = ../common/mboot_usbd.c fake_tusb.c test_usbd.c

test_runner: $(SRCS_COMMON) $(SRCS_SUPPORT) $(SRCS_TESTS) $(SRCS_USBD)
	$(CC) $(CFLAGS) -DMBOOT_TESTS_FAKE_TUSB=1 -o $@ $^

test_runner_pack: $(SRCS_COMMON) $(SRCS_PACK)
	$(CC) $(CFLAGS) -DMBOOT_ENABLE_PACKING=1 -o $@ \
	    ../common/mboot_dfu.c ../common/mboot_region.c \
	    $(SRCS_PACK)

check: test_runner test_runner_pack
	./test_runner
	./test_runner_pack
	python3 test_pydfu_wire.py

clean:
	rm -f test_runner test_runner_pack

.PHONY: check clean
