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

# Makefile for ports/rp2/mboot — thin CMake wrapper.
#
# Mirrors the style of ports/rp2/Makefile.  Invokes cmake with the
# appropriate MICROPY_BOARD / MICROPY_BOARD_DIR, then builds via the
# generated Makefile in the build directory.

# Select the board to build for.  Per-board mboot config lives in the main
# rp2 board dir (ports/rp2/boards/<BOARD>) alongside mpconfigboard.{h,cmake};
# the CMakeLists.txt in this directory enforces that the board opts in via
# `set(USE_MBOOT 1)`.
ifdef BOARD_DIR
BOARD ?= $(notdir $(BOARD_DIR:/=))
else
BOARD ?= RPI_PICO
BOARD_DIR ?= ../boards/$(BOARD)
endif

ifeq ($(wildcard $(BOARD_DIR)/.),)
$(error Invalid BOARD specified: $(BOARD_DIR))
endif

# Build directory per-board.
BUILD ?= build-$(BOARD)

ifeq ($(BUILD_VERBOSE),1)
MAKE_ARGS += VERBOSE=1
endif

CMAKE_ARGS += -DMICROPY_BOARD=$(BOARD) -DMICROPY_BOARD_DIR="$(abspath $(BOARD_DIR))"

ifeq ($(DEBUG),1)
CMAKE_ARGS += -DCMAKE_BUILD_TYPE=Debug
endif

ifdef PICO_SDK_PATH
CMAKE_ARGS += -DPICO_SDK_PATH_OVERRIDE=$(PICO_SDK_PATH)
endif

HELP_PICO_SDK_SUBMODULE ?= "Error: pico-sdk submodule is not initialized. Run 'make submodules' from ports/rp2."

all:
	$(Q)[ -f ../../../lib/pico-sdk/README.md ] || (echo $(HELP_PICO_SDK_SUBMODULE); false)
	$(Q)[ -e $(BUILD)/Makefile ] || cmake -S . -B $(BUILD) -DPICO_BUILD_DOCS=0 $(CMAKE_ARGS)
	$(Q)$(MAKE) $(MAKE_ARGS) -C $(BUILD)

clean:
	$(RM) -rf $(BUILD)

.PHONY: all clean
