# Test micropython.repl_readline_push_history with history recall.
try:
    import micropython

    micropython.repl_readline_push_history
    micropython.repl_readline_init
    micropython.repl_readline_process_char
    micropython.repl_readline_get_line
except (ImportError, AttributeError):
    print("SKIP")
    raise SystemExit

# Push a line into history
micropython.repl_readline_push_history("hello world")

# Start a new readline session and press up-arrow (ESC [ A) to recall
micropython.repl_readline_init("")
micropython.repl_readline_process_char(0x1B)  # ESC
micropython.repl_readline_process_char(ord("["))
micropython.repl_readline_process_char(ord("A"))  # Up arrow
# Now complete the line
micropython.repl_readline_process_char(ord("\r"))
line = micropython.repl_readline_get_line()
print(repr(line))

# Push a second entry and verify both are in history
micropython.repl_readline_push_history("second line")
micropython.repl_readline_init("")
micropython.repl_readline_process_char(0x1B)
micropython.repl_readline_process_char(ord("["))
micropython.repl_readline_process_char(ord("A"))  # Most recent
ret = micropython.repl_readline_process_char(ord("\r"))
print(repr(micropython.repl_readline_get_line()))

# Duplicate suppression: pushing same line again shouldn't add a duplicate
micropython.repl_readline_push_history("second line")
micropython.repl_readline_init("")
micropython.repl_readline_process_char(0x1B)
micropython.repl_readline_process_char(ord("["))
micropython.repl_readline_process_char(ord("A"))  # Should still be "second line"
micropython.repl_readline_process_char(0x1B)
micropython.repl_readline_process_char(ord("["))
micropython.repl_readline_process_char(ord("A"))  # Should be "hello world"
micropython.repl_readline_process_char(ord("\r"))
print(repr(micropython.repl_readline_get_line()))
