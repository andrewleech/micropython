# test sys.settrace with local variable access by index

import sys

try:
    sys.settrace
except AttributeError:
    print("SKIP")
    raise SystemExit

# Global test data storage
trace_events = []
local_vars_data = []


def trace_handler(frame, event, arg):
    """Trace handler that captures local variables by index."""
    # Skip importlib and other internal modules
    if frame.f_globals.get("__name__", "").find("importlib") != -1:
        return trace_handler

    # Record the event and local variables
    event_data = {
        'event': event,
        'function': frame.f_code.co_name,
        'lineno': frame.f_lineno,
        'locals': dict(frame.f_locals),  # Capture locals by index
    }
    trace_events.append(event_data)

    return trace_handler


def test_basic_locals():
    """Test basic local variable capture."""

    def simple_func():
        a = 42
        b = "hello"
        c = [1, 2, 3]
        return a + len(b) + len(c)

    # Clear previous data
    trace_events.clear()

    # Enable tracing and run function
    sys.settrace(trace_handler)
    result = simple_func()
    sys.settrace(None)

    # Find the call event for simple_func
    call_events = [
        e for e in trace_events if e['event'] == 'call' and e['function'] == 'simple_func'
    ]
    line_events = [
        e for e in trace_events if e['event'] == 'line' and e['function'] == 'simple_func'
    ]

    print("test_basic_locals:")
    print(f"  Function returned: {result}")
    print(f"  Call events: {len(call_events)}")
    print(f"  Line events: {len(line_events)}")

    # Check that we captured local variables by index
    if line_events:
        last_line_event = line_events[-1]
        locals_dict = last_line_event['locals']
        print(f"  Local variables found: {sorted(locals_dict.keys())}")

        # Verify index-based naming (should be local_00, local_01, etc.)
        index_keys = [k for k in locals_dict.keys() if k.startswith('local_')]
        print(f"  Index-based locals: {sorted(index_keys)}")

        # Print actual values
        for key in sorted(index_keys):
            print(f"    {key}: {locals_dict[key]} ({type(locals_dict[key]).__name__})")


def test_nested_function_locals():
    """Test local variable capture in nested functions."""

    def outer_func(x):
        outer_var = x * 2

        def inner_func(y):
            inner_var = y + outer_var
            return inner_var

        result = inner_func(10)
        return result

    # Clear previous data
    trace_events.clear()

    # Enable tracing and run function
    sys.settrace(trace_handler)
    result = outer_func(5)
    sys.settrace(None)

    print("\ntest_nested_function_locals:")
    print(f"  Function returned: {result}")

    # Analyze events for both functions
    outer_events = [e for e in trace_events if e['function'] == 'outer_func']
    inner_events = [e for e in trace_events if e['function'] == 'inner_func']

    print(f"  Outer function events: {len(outer_events)}")
    print(f"  Inner function events: {len(inner_events)}")

    # Check locals in each function
    for func_name, events in [('outer_func', outer_events), ('inner_func', inner_events)]:
        line_events = [e for e in events if e['event'] == 'line']
        if line_events:
            last_event = line_events[-1]
            locals_dict = last_event['locals']
            index_keys = [k for k in locals_dict.keys() if k.startswith('local_')]
            print(f"  {func_name} locals: {sorted(index_keys)}")
            for key in sorted(index_keys):
                print(f"    {key}: {locals_dict[key]}")


def test_loop_locals():
    """Test local variable capture in loops."""

    def loop_func():
        total = 0
        for i in range(3):
            temp = i * 2
            total += temp
        return total

    # Clear previous data
    trace_events.clear()

    # Enable tracing and run function
    sys.settrace(trace_handler)
    result = loop_func()
    sys.settrace(None)

    print("\ntest_loop_locals:")
    print(f"  Function returned: {result}")

    # Find line events in the loop
    loop_events = [
        e for e in trace_events if e['function'] == 'loop_func' and e['event'] == 'line'
    ]

    print(f"  Line events in loop: {len(loop_events)}")

    # Check locals evolution through loop iterations
    for i, event in enumerate(loop_events[-3:]):  # Last few events
        locals_dict = event['locals']
        index_keys = [k for k in locals_dict.keys() if k.startswith('local_')]
        print(f"  Event {i} (line {event['lineno']}) locals: {sorted(index_keys)}")
        for key in sorted(index_keys):
            value = locals_dict[key]
            print(f"    {key}: {value} ({type(value).__name__})")


def test_exception_locals():
    """Test local variable capture when exceptions occur."""

    def exception_func():
        x = 100
        y = 0
        try:
            result = x / y  # This will raise ZeroDivisionError
        except ZeroDivisionError:
            error_handled = True
            return -1

    # Clear previous data
    trace_events.clear()

    # Enable tracing and run function
    sys.settrace(trace_handler)
    result = exception_func()
    sys.settrace(None)

    print("\ntest_exception_locals:")
    print(f"  Function returned: {result}")

    # Find exception-related events
    exception_events = [e for e in trace_events if e['function'] == 'exception_func']
    line_events = [e for e in exception_events if e['event'] == 'line']

    print(f"  Events in exception function: {len(exception_events)}")

    # Check locals in exception handling
    if line_events:
        last_event = line_events[-1]
        locals_dict = last_event['locals']
        index_keys = [k for k in locals_dict.keys() if k.startswith('local_')]
        print(f"  Final locals: {sorted(index_keys)}")
        for key in sorted(index_keys):
            print(f"    {key}: {locals_dict[key]}")


def test_parameter_locals():
    """Test that function parameters appear in locals."""

    def param_func(arg1, arg2, *args, **kwargs):
        local_var = arg1 + arg2
        return local_var

    # Clear previous data
    trace_events.clear()

    # Enable tracing and run function
    sys.settrace(trace_handler)
    result = param_func(10, 20, 30, key="value")
    sys.settrace(None)

    print("\ntest_parameter_locals:")
    print(f"  Function returned: {result}")

    # Find events for the function
    func_events = [e for e in trace_events if e['function'] == 'param_func']
    line_events = [e for e in func_events if e['event'] == 'line']

    if line_events:
        # Check locals after parameter setup
        first_line_event = line_events[0]
        locals_dict = first_line_event['locals']
        index_keys = [k for k in locals_dict.keys() if k.startswith('local_')]
        print(f"  Locals with parameters: {sorted(index_keys)}")
        for key in sorted(index_keys):
            value = locals_dict[key]
            print(f"    {key}: {value} ({type(value).__name__})")


# Run all tests
print("=== Testing sys.settrace local variable access by index ===")
test_basic_locals()
test_nested_function_locals()
test_loop_locals()
test_exception_locals()
test_parameter_locals()
print("\n=== Tests completed ===")
