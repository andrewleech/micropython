# test sys.settrace() local variable name preservation
# this test requires MICROPY_PY_SYS_SETTRACE and MICROPY_PY_SYS_SETTRACE_LOCALNAMES

import sys

try:
    sys.settrace
except AttributeError:
    print("SKIP")
    raise SystemExit

# Test basic local variable name preservation
trace_events = []

def trace_handler(frame, event, arg):
    if frame.f_code.co_name == 'test_function':
        locals_dict = frame.f_locals
        if locals_dict:
            # Check if we have real variable names (not just local_XX)
            real_names = [k for k in locals_dict.keys() if not k.startswith('local_')]
            if real_names:
                trace_events.append((event, sorted(real_names)))
        else:
            trace_events.append((event, []))
    return trace_handler

def test_function():
    username = "Alice"
    age = 25
    return username, age

# Test 1: Basic functionality
trace_events.clear()
sys.settrace(trace_handler)
result = test_function()
sys.settrace(None)

print("Test 1: Basic local variable names")
print(result == ("Alice", 25))

# Check if we captured any real variable names
has_real_names = any(names for _, names in trace_events if names)
print(has_real_names)

if has_real_names:
    # Find the event with the most variables
    max_vars_event = max(trace_events, key=lambda x: len(x[1]), default=(None, []))
    if max_vars_event[1]:
        # Check if expected variables are present
        expected = {'username', 'age'}
        actual = set(max_vars_event[1])
        print(expected.issubset(actual))
    else:
        print(False)
else:
    # If no real names, check we got fallback behavior (local_XX names)
    print("fallback")

# Test 2: Nested function variables
def test_nested():
    outer_var = "outer"
    def inner():
        inner_var = "inner"
        return inner_var
    return outer_var, inner()

trace_events.clear()
sys.settrace(trace_handler)
result2 = test_nested()
sys.settrace(None)

print("Test 2: Nested function test")
print(result2 == ("outer", "inner"))

# Test 3: sys.settrace with no callback should not crash
sys.settrace(None)
def simple_test():
    x = 42
    return x

result3 = simple_test()
print("Test 3: No trace callback")
print(result3 == 42)

# Test 4: Frame access without crash
def frame_access_test():
    def trace_frame(frame, event, arg):
        if frame.f_code.co_name == 'inner_func':
            # Access frame attributes safely
            try:
                locals_dict = frame.f_locals
                # This should not crash
                return trace_frame
            except:
                return None
        return trace_frame
    
    def inner_func():
        test_var = "test"
        return test_var
    
    sys.settrace(trace_frame)
    result = inner_func()
    sys.settrace(None)
    return result

result4 = frame_access_test()
print("Test 4: Frame access safety")
print(result4 == "test")

print("All tests completed")