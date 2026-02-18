# Test micropython.repl_readline_push/pop for reentrant readline.
# Requires MICROPY_REPL_ASYNCIO_BREAKPOINT to expose push/pop to Python.
import micropython

try:
    micropython.repl_readline_push
except AttributeError:
    print("SKIP")
    raise SystemExit

# Test basic push/pop API
print(micropython.repl_readline_push())  # True
print(micropython.repl_readline_push())  # False (already saved)
micropython.repl_readline_pop()
print(micropython.repl_readline_push())  # True (slot free again)
micropython.repl_readline_pop()

# Test state isolation: inner session works after push
micropython.repl_readline_init("", "... ")
micropython.repl_readline_process_char(ord("h"))
micropython.repl_readline_process_char(ord("i"))

micropython.repl_readline_push()
micropython.repl_readline_init("", "... ")
micropython.repl_readline_process_char(ord("x"))
result = micropython.repl_readline_process_char(0x0D)
print(result[0])  # x
micropython.repl_readline_pop()

# Test pop without push is a no-op
micropython.repl_readline_pop()
print("no crash")
