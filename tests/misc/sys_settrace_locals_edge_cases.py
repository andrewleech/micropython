# Test edge cases for sys.settrace frame.f_locals

import sys

try:
    sys.settrace
except AttributeError:
    print("SKIP")
    raise SystemExit


def trace_handler(frame, event, arg):
    """Trace handler for edge case testing."""
    if frame.f_globals.get("__name__", "").find("importlib") != -1:
        return trace_handler

    if frame.f_code.co_name.startswith('test_'):
        locals_dict = frame.f_locals
        # Check if f_locals returns a dict
        print(f"{event}:{frame.f_code.co_name} f_locals_type={type(locals_dict).__name__}")

        # Count local variables in scope
        if hasattr(locals_dict, 'keys'):
            local_keys = sorted(locals_dict.keys())
            print(f"  local_vars={len(local_keys)}")

            # Spot-check the first couple of entries
            for key in local_keys[:2]:
                print(f"  {key}={locals_dict[key]}")

    return trace_handler


def test_empty_function():
    """Function with no local variables."""
    pass


def test_single_var():
    """Function with one local variable."""
    x = 100


def test_none_values():
    """Function with None values."""
    a = None
    b = 42
    c = None


def test_complex_types():
    """Function with complex data types."""
    lst = [1, 2, 3]
    dct = {'key': 'value'}
    tpl = (1, 2, 3)


print("=== Edge case testing ===")

sys.settrace(trace_handler)
test_empty_function()
test_single_var()
test_none_values()
test_complex_types()
sys.settrace(None)

print("=== Edge cases completed ===")
