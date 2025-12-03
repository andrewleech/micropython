# Manifest for pydfu standalone application.
# Build with: make -C ports/unix romfs ROMFS_DIR=tools/pydfu_app
# Or use this manifest directly for frozen builds.

# Add unix-ffi library for pyusb and other FFI-based modules
add_library("unix-ffi", "$(MPY_LIB_DIR)/unix-ffi")

# Required modules for pydfu
require("pyusb")      # USB device access
require("argparse")   # Command-line argument parsing
require("re")         # Regular expressions for memory layout parsing

# The pydfu module itself is in lib/pydfu.py within the app directory
# and main.py is the entry point - these get included via romfs directory
