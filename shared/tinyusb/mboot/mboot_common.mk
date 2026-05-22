# This file is part of the MicroPython project, http://micropython.org/
#
# The MIT License (MIT)
#
# Copyright (c) 2026 Andrew Leech
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in
# all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
# THE SOFTWARE.

# mboot_common.mk — build fragment for shared/tinyusb/mboot.
#
# Per-port adapter Makefiles include this fragment and then append the
# exported variables to their own INC, SRC_C, and CFLAGS:
#
#   include $(TOP)/shared/tinyusb/mboot/mboot_common.mk
#   INC += $(SHARED_TINYUSB_MBOOT_INC)
#   SRC_C += $(SHARED_TINYUSB_MBOOT_SRC_C)
#   CFLAGS += $(SHARED_TINYUSB_MBOOT_CFLAGS)
#
# This fragment defines no Make rules.  It is composable: including it
# multiple times is safe because the variables are assigned with := (simple
# expansion) and are not conditionally guarded, but re-inclusion will
# overwrite them with identical values.
#
# Pack support (MBOOT_ENABLE_PACKING) is opt-in per port and is not enabled
# by this fragment.  To enable it, add -DMBOOT_ENABLE_PACKING=1 to CFLAGS
# and link mboot_pack.c from your port-specific Stage 3 implementation.

# Require that TOP is set by the including Makefile.
ifeq ($(TOP),)
$(error mboot_common.mk: TOP is not set; set TOP to the root of the MicroPython tree)
endif

SHARED_TINYUSB_MBOOT_DIR := $(TOP)/shared/tinyusb/mboot

SHARED_TINYUSB_MBOOT_INC := -I$(SHARED_TINYUSB_MBOOT_DIR)/include

SHARED_TINYUSB_MBOOT_SRC_C := \
    shared/tinyusb/mboot/common/mboot_dbl_tap.c \
    shared/tinyusb/mboot/common/mboot_dfu.c \
    shared/tinyusb/mboot/common/mboot_region.c \
    shared/tinyusb/mboot/common/mboot_elem.c \
    shared/tinyusb/mboot/common/mboot_usbd.c

# The including port MUST define CFG_TUD_DFU=1 in its tusb_config.h and link
# lib/tinyusb/src/class/dfu/dfu_device.c.  mboot_usbd.c implements the
# standard tud_dfu_*_cb callbacks and the new tud_dfu_set_alt_cb hook.

# Pack support is opt-in per port; default off.
SHARED_TINYUSB_MBOOT_CFLAGS ?=
