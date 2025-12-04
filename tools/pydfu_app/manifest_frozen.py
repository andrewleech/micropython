# Frozen manifest for pydfu standalone application.
# Use with: make FROZEN_MANIFEST=tools/pydfu_app/manifest_frozen.py
#
# This freezes all Python dependencies into the binary.
# RomFS is only needed for non-Python files (libusb DLL on Windows).

# Include standard Unix variant modules
include("$(PORT_DIR)/variants/manifest.py")

# Add unix-ffi library for pyusb
add_library("unix-ffi", "$(MPY_LIB_DIR)/unix-ffi")

# Required modules from micropython-lib
require("argparse")   # Command-line argument parsing
require("os-path")    # os.path module

# Freeze pydfu application code
module("main.py", base_path="$(MPY_DIR)/tools/pydfu_app")
module("pydfu.py", base_path="$(MPY_DIR)/tools/pydfu_app/lib")
module("zlib.py", base_path="$(MPY_DIR)/tools/pydfu_app/lib")

# Use local usb package with Windows DLL extraction support
# (micropython-lib's pyusb doesn't check usb._native_lib_dir)
package("usb", base_path="$(MPY_DIR)/tools/pydfu_app/lib")
