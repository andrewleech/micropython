# Location of top-level MicroPython directory
MPY_DIR = ../../..

# Name of module
MOD = rtt_$(ARCH)

# Source files (.c or .py) - using patched RTT implementation
SRC = rtt.c SEGGER_RTT_dynamic.c

# Architecture to build for (x86, x64, armv7m, xtensa, xtensawin, rv32imc)
ARCH ?= x64

# Note: We use locally patched RTT files instead of downloading originals
# Original RTT source: https://github.com/SEGGERMicro/RTT/tree/main/RTT
# Modifications made for MicroPython native module compatibility (heap allocation)

# Add RTT include directory for our source
CFLAGS += -I.

# Link with standard library for some targets
ifeq ($(ARCH),armv6m)
LINK_RUNTIME = 1
endif

ifeq ($(ARCH),xtensa)
LINK_RUNTIME = 1
MPY_EXTERN_SYM_FILE=$(MPY_DIR)/ports/esp8266/boards/eagle.rom.addr.v6.ld
endif

include $(MPY_DIR)/py/dynruntime.mk