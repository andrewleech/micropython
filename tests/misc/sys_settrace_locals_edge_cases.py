# Test edge cases for sys.settrace local variable access

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

        # Count local variables by index
        if hasattr(locals_dict, 'keys'):
            index_keys = [k for k in locals_dict.keys() if k.startswith('local_')]
            print(f"  index_vars={len(index_keys)}")

            # Test accessing specific indices
            if 'local_00' in locals_dict:
                print(f"  local_00={locals_dict['local_00']}")
            if 'local_01' in locals_dict:
                print(f"  local_01={locals_dict['local_01']}")

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
