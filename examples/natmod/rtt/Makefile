# Location of top-level MicroPython directory
MPY_DIR = ../../..

# Name of module
MOD = rtt_$(ARCH)

# Source files (.c or .py)
SRC = rtt.c SEGGER_RTT.c

# Architecture to build for (x86, x64, armv7m, xtensa, xtensawin, rv32imc)
ARCH ?= x64

# RTT source files from SEGGER GitHub repository
RTT_BASE_URL = https://raw.githubusercontent.com/SEGGERMicro/RTT/main/RTT

RTT_SOURCES = \
	SEGGER_RTT.c \
	SEGGER_RTT.h \
	SEGGER_RTT_Conf.h \
	SEGGER_RTT_printf.c

# Download RTT source files if they don't exist
$(RTT_SOURCES):
	@echo "Downloading RTT source: $@"
	@curl -L -o $@ $(RTT_BASE_URL)/$@ || { echo "Failed to download $@"; exit 1; }

# Ensure RTT sources are downloaded before building our source
rtt.c: | $(RTT_SOURCES)

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

# Clean rule to remove downloaded files
clean-rtt:
	rm -f $(RTT_SOURCES)

.PHONY: clean-rtt

include $(MPY_DIR)/py/dynruntime.mk

# Add clean-rtt to the main clean target
clean: clean-rtt