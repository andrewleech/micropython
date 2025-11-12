# Include default manifests
include("$(PORT_DIR)/boards/manifest.py")
include("$(PORT_DIR)/boards/manifest_pyboard.py")

# Freeze enum module for size testing
freeze("../../../lib/enum", "enum.py")
