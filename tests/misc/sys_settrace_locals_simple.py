# Simple test for sys.settrace frame.f_locals - minimal output for comparison

import sys

try:
    sys.settrace
except AttributeError:
    print("SKIP")
    raise SystemExit


def trace_handler(frame, event, arg):
    """Simple trace handler that shows the local variable names in scope."""
    # Skip internal modules
    if frame.f_globals.get("__name__", "").find("importlib") != -1:
        return trace_handler

    # Only trace our test functions
    if frame.f_code.co_name.startswith('test_'):
        locals_dict = frame.f_locals
        local_keys = sorted(locals_dict.keys())
        if local_keys:
            print(f"{event}:{frame.f_code.co_name}:{frame.f_lineno} locals={local_keys}")

    return trace_handler


def test_simple():
    a = 42
    b = "hello"
    return a


def test_with_loop():
    total = 0
    for i in range(2):
        total += i
    return total


# Run tests
sys.settrace(trace_handler)
test_simple()
test_with_loop()
sys.settrace(None)
print("done")
