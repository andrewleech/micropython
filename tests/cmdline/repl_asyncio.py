# Test the asyncio REPL, which runs via the compiler's top-level-await support.
# If the standard (non-async) REPL were running, await would be a SyntaxError.
# A multi-line async def and tuple-unpacking assignment from await both work
# here (the earlier text-heuristic REPL mishandled them).
await asyncio.sleep(0)
async def f():
    await asyncio.sleep(0)
    return 42

r = await f()
print("r =", r)
a, b = (await f(), 99)
print("a, b =", a, b)
await f()
print("async_ok")
