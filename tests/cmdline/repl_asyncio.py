# Test that the asyncio REPL boots and can evaluate await expressions.
# If the standard (non-async) REPL were running, await would be a SyntaxError.
await asyncio.sleep(0)
print("async_ok")
