include("$(PORT_DIR)/variants/manifest.py")

include("$(MPY_DIR)/extmod/asyncio")

# Include enum module
module("enum.py", base_path="$(MPY_DIR)/lib/enum")
