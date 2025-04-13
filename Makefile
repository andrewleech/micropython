# Top-level Makefile for MicroPython Kconfig integration

# Default Kconfig file name
KCONFIG_CONFIG ?= .config
export KCONFIG_CONFIG

# Source directory
SRCTREE := $(CURDIR)
export SRCTREE

# Build output directory
BUILD_DIR := $(SRCTREE)/build
export KCONFIG_BUILD_DIR=$(BUILD_DIR)

# Kconfig tools prefix (can be customized, e.g., kconfig- if commands are kconfig-mconf)
KCONFIG_PREFIX ?= kconfig-

# Kconfig configuration tool
KCONFIG_CONF := $(KCONFIG_PREFIX)conf
# Kconfig menuconfig tool
KCONFIG_MENUCONFIG := $(KCONFIG_PREFIX)mconf
# Kconfig xconfig tool (requires GUI)
KCONFIG_XCONFIG := $(KCONFIG_PREFIX)qconf # qconf is common for xconfig

# Ensure build directory exists
$(shell mkdir -p $(BUILD_DIR))

# Include generated Make configuration if it exists
-include $(BUILD_DIR)/autoconf.mk

# Define PORT variable if not set (e.g. from environment or command line)
# This ensures Kconfig can select the port even if make is run without PORT=
ifeq ($(PORT),)
    ifeq ($(CONFIG_PORT_UNIX),y)
        PORT := unix
    else ifeq ($(CONFIG_PORT_STM32),y)
        PORT := stm32
    else ifeq ($(CONFIG_PORT_ESP32),y)
        PORT := esp32
    # Add other ports here...
    else
        # Default port if none selected or found in .config
        PORT ?= unix
    endif
endif
export PORT

# Phony targets for Kconfig interfaces
.PHONY: menuconfig xconfig oldconfig syncconfig savedefconfig all clean help

menuconfig:
	$(KCONFIG_MENUCONFIG) Kconfig
	xargs -r -- $(KCONFIG_CONF) --syncconfig Kconfig < $(KCONFIG_CONFIG) || \
	    $(KCONFIG_CONF) --syncconfig Kconfig
	@echo "Configuration saved to $(KCONFIG_CONFIG)"

xconfig:
	$(KCONFIG_XCONFIG) Kconfig
	xargs -r -- $(KCONFIG_CONF) --syncconfig Kconfig < $(KCONFIG_CONFIG) || \
	    $(KCONFIG_CONF) --syncconfig Kconfig
	@echo "Configuration saved to $(KCONFIG_CONFIG)"

oldconfig:
	$(KCONFIG_CONF) --oldconfig Kconfig
	@echo "Configuration updated based on $(KCONFIG_CONFIG)"

syncconfig:
	$(KCONFIG_CONF) --syncconfig Kconfig
	@echo "Generated outputs in $(BUILD_DIR)"

savedefconfig:
	$(KCONFIG_CONF) --savedefconfig=$(BUILD_DIR)/defconfig Kconfig
	@echo "Default configuration saved to $(BUILD_DIR)/defconfig"

# Generate configuration outputs if .config exists and is newer than outputs
# or if outputs don't exist.
$(BUILD_DIR)/mpconfig_kconfig.h $(BUILD_DIR)/autoconf.mk $(BUILD_DIR)/config.cmake: $(KCONFIG_CONFIG) Kconfig FORCE
	@echo "Generating Kconfig outputs..."
	$(Q)$(KCONFIG_CONF) --syncconfig Kconfig

FORCE:

# Default target: Delegate to the selected port's Makefile
# Pass all other arguments to the port's make
all: $(BUILD_DIR)/mpconfig_kconfig.h $(BUILD_DIR)/autoconf.mk $(BUILD_DIR)/config.cmake
	@echo "Building for PORT=$(PORT)"
	$(MAKE) -C ports/$(PORT) $(MAKECMDGOALS)

# Clean target: Clean build directory and delegate to port
clean:
	@echo "Cleaning build directory $(BUILD_DIR)"
	rm -rf $(BUILD_DIR)
	rm -f $(KCONFIG_CONFIG)
	@if [ -d "ports/$(PORT)" ]; then \
		$(MAKE) -C ports/$(PORT) clean; \
	fi

# Help target
help:
	@echo "MicroPython Top-Level Makefile"
	@echo ""
	@echo "Usage:"
	@echo "  make [PORT=<port>] [BOARD=<board>] [target] ..."
	@echo ""
	@echo "Kconfig Targets:"
	@echo "  menuconfig       - Update configuration using interactive menu (requires ncurses)"
	@echo "  xconfig          - Update configuration using interactive graphical menu (requires Qt/GTK)"
	@echo "  oldconfig        - Update configuration based on current .config file"
	@echo "  syncconfig       - Generate configuration outputs (header, make include, cmake include) from .config"
	@echo "  savedefconfig    - Save current configuration as a minimal defconfig"
	@echo ""
	@echo "Build Targets:"
	@echo "  all (default)    - Build the selected port (determined by PORT= or Kconfig)"
	@echo "  clean            - Clean build outputs for the selected port and Kconfig"
	@echo "  <other_target>   - Run target in the selected port's Makefile (e.g., test, deploy)"
	@echo ""
	@echo "Variables:"
	@echo "  PORT             - Specify the target port (e.g., unix, stm32, esp32). Overrides Kconfig selection."
	@echo "  BOARD            - Specify the target board for ports that require it."
	@echo "  KCONFIG_CONFIG   - Path to the Kconfig configuration file (default: .config)"
	@echo "  KCONFIG_PREFIX   - Prefix for Kconfig tools (e.g., if installed in a specific location)"
	@echo ""

# Include this at the end to allow overriding targets/variables
# Make sure .config exists before potentially including autoconf.mk
ifeq ($(wildcard $(KCONFIG_CONFIG)),$(KCONFIG_CONFIG))
-include $(BUILD_DIR)/autoconf.mk
endif
