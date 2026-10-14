# Freeze the asyncio REPL by default; flash-tight boards can opt out by
# including this manifest with repl_asyncio=False (and MICROPY_REPL_ASYNCIO=0).
options.defaults(repl_asyncio=True)
include("$(MPY_DIR)/extmod/asyncio", repl_asyncio=options.repl_asyncio)

require("dht")
require("onewire")
