# Unix build with the mcuboot module over a file backed fake flash, for host tests of
# extmod/modmcuboot.c and shared/mcuboot/src/app_api.c with the single slot policy
# (tests/mcuboot/test_module_single.py).
# The flash layout is the one of mpconfigboard.h and mcuboot_dev.h in this directory.
#
#   make -C ports/unix VARIANT=mcuboot_single

FROZEN_MANIFEST ?= variants/manifest.py

MICROPY_PY_MCUBOOT = 1

MCUBOOT_ROLE = app
BOARD_DIR = $(VARIANT_DIR)
MCUBOOT_DEV_DIR = $(VARIANT_DIR)
include $(TOP)/shared/mcuboot/mcuboot_common.mk

INC += $(MCUBOOT_INC)
CFLAGS += $(MCUBOOT_CFLAGS)
SRC_C += $(MCUBOOT_SRC_C) $(MCUBOOT_GEN_SRC_C) tests/mcuboot/unix_flash.c
