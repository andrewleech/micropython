include("$(PORT_DIR)/boards/manifest.py")

require("sdcard")

# Networking
require("bundle-networking")

# ESP32-C6 AirLift firmware updater
require("espflash")
