# Test micropython.stdio_mode_raw doesn't crash.
try:
    import micropython

    micropython.stdio_mode_raw
except (ImportError, AttributeError):
    print("SKIP")
    raise SystemExit

# Enable raw mode then restore original.
# On Unix this changes terminal settings; when stdin is a pipe (test runner)
# it may silently fail but should not crash.
micropython.stdio_mode_raw(True)
micropython.stdio_mode_raw(False)
print("stdio_mode_raw_ok")

# Double-disable is safe
micropython.stdio_mode_raw(False)
print("double_disable_ok")
