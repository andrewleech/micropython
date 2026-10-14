# Test that micropython.repl() (the blocking breakpoint REPL) exists and is
# callable.  The interactive REPL can't be driven from the test runner (it
# reads stdin, which is the test script), so this is a presence smoke check.

try:
    import micropython

    micropython.repl
except (ImportError, AttributeError):
    print("SKIP")
    raise SystemExit

# We can't test the interactive REPL directly in the test runner since
# it reads from stdin which is the test script itself. Instead verify
# the function exists and is callable.

# Verify repl is a callable
print(callable(micropython.repl))

print("repl_breakpoint_ok")
