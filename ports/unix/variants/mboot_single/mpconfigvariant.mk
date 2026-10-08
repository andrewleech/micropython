# Unix build with the mboot module over a file backed fake flash, for host tests of
# extmod/modmboot.c and shared/mboot/src/app_api.c with the single slot policy
# (tests/mboot/test_module_single.py).
# The flash layout is the one of mpconfigboard.h and mboot_dev.h in this directory.
#
#   make -C ports/unix VARIANT=mboot_single

FROZEN_MANIFEST ?= variants/manifest.py

MICROPY_PY_MBOOT = 1

MBOOT_ROLE = app
BOARD_DIR = $(VARIANT_DIR)
MBOOT_DEV_DIR = $(VARIANT_DIR)
include $(TOP)/shared/mboot/mboot_common.mk

INC += $(MBOOT_INC)
CFLAGS += $(MBOOT_CFLAGS)
SRC_C += $(MBOOT_SRC_C) $(MBOOT_GEN_SRC_C) tests/mboot/unix_flash.c
