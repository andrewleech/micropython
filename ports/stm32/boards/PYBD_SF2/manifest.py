# Insufficient internal flash to also carry the asyncio REPL (see
# MICROPY_REPL_ASYNCIO=0 in mpconfigboard.h), so don't freeze arepl.py.
include("$(PORT_DIR)/boards/manifest.py", repl_asyncio=False)
include("$(PORT_DIR)/boards/manifest_pyboard.py")
require("bundle-networking")
