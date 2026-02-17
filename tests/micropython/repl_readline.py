# Check that micropython.repl_readline_* functions work as expected.
try:
    import micropython

    micropython.repl_readline_init
    micropython.repl_readline_process_char
except (ImportError, AttributeError):
    print("SKIP")
    raise SystemExit

# Test basic readline flow: init, feed chars, check return values.
# Use empty prompts to reduce output noise from readline echo.
micropython.repl_readline_init("", "")
for c in "hello":
    ret = micropython.repl_readline_process_char(ord(c))
    assert ret == -1, ret
# Complete the line with CR — returns (line, 0) tuple.
result = micropython.repl_readline_process_char(ord("\r"))
print(repr(result[0]))
print(result[1])

# Test control codes on empty line — returns (line, ctrl_code) tuple.
micropython.repl_readline_init("", "")
result = micropython.repl_readline_process_char(3)  # Ctrl-C
print(result[1])
micropython.repl_readline_init("", "")
result = micropython.repl_readline_process_char(4)  # Ctrl-D
print(result[1])
micropython.repl_readline_init("", "")
result = micropython.repl_readline_process_char(1)  # Ctrl-A
print(result[1])
micropython.repl_readline_init("", "")
result = micropython.repl_readline_process_char(5)  # Ctrl-E
print(result[1])

# Test that C-side continuation detection works: "if True:" needs more input.
micropython.repl_readline_init("", "")
for c in "if True:":
    ret = micropython.repl_readline_process_char(ord(c))
    assert ret == -1, ret
# CR on a line needing continuation returns -1 (C handles continuation internally).
ret = micropython.repl_readline_process_char(ord("\r"))
print(ret)

print("readline_ok")
