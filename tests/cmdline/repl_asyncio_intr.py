# Test that Ctrl-C cancels a running top-level await in the asyncio REPL.
# The REPL runs in raw mode, so Ctrl-C arrives as a byte that the driver's
# interrupt watcher reads to cancel the coroutine (not delivered as SIGINT).
await asyncio.sleep(10)
{\x03}
print("cancelled")
