#!/usr/bin/env python3
"""
Test script for local variable name preservation in sys.settrace()
"""

import sys

# Test data collection
trace_events = []


def trace_handler(frame, event, arg):
    """Trace handler that captures local variables with names."""
    # Skip internal modules
    if frame.f_globals.get("__name__", "").find("importlib") != -1:
        return trace_handler

    # Only trace our test functions
    if frame.f_code.co_name.startswith('test_'):
        locals_dict = frame.f_locals
        event_data = {
            'event': event,
            'function': frame.f_code.co_name,
            'lineno': frame.f_lineno,
            'locals': dict(locals_dict),
            'has_real_names': any(not k.startswith('local_') for k in locals_dict.keys()),
        }
        trace_events.append(event_data)

    return trace_handler


def test_basic_locals():
    """Test basic local variable name preservation."""

    def simple_func():
        username = "Alice"
        age = 25
        score = 95.5
        return username, age, score

    # Clear previous data
    trace_events.clear()

    # Enable tracing and run function
    sys.settrace(trace_handler)
    result = simple_func()
    sys.settrace(None)

    print("=== test_basic_locals ===")
    print(f"Function returned: {result}")

    # Analyze the last line event (most variables should be present)
    line_events = [
        e for e in trace_events if e['event'] == 'line' and e['function'] == 'simple_func'
    ]

    if line_events:
        last_event = line_events[-1]
        locals_dict = last_event['locals']
        has_real_names = last_event['has_real_names']

        print(f"Has real variable names: {has_real_names}")
        print(f"Local variables captured: {sorted(locals_dict.keys())}")

        # Check for expected variable names
        expected_names = {'username', 'age', 'score'}
        actual_names = set(locals_dict.keys())

        if expected_names.issubset(actual_names):
            print("✓ SUCCESS: All expected variable names found!")
            for name in expected_names:
                print(f"  {name}: {locals_dict[name]} ({type(locals_dict[name]).__name__})")
        else:
            print("⚠ PARTIAL: Some names missing or using index fallback")
            for key, value in sorted(locals_dict.items()):
                print(f"  {key}: {value} ({type(value).__name__})")

    return trace_events


def test_nested_functions():
    """Test local variable names in nested functions."""

    def outer_func(param1):
        outer_var = param1 * 2

        def inner_func(param2):
            inner_var = param2 + outer_var
            return inner_var

        result = inner_func(10)
        return result

    # Clear previous data
    trace_events.clear()

    # Enable tracing and run function
    sys.settrace(trace_handler)
    result = outer_func(5)
    sys.settrace(None)

    print("\n=== test_nested_functions ===")
    print(f"Function returned: {result}")

    # Analyze both functions
    outer_events = [e for e in trace_events if e['function'] == 'outer_func']
    inner_events = [e for e in trace_events if e['function'] == 'inner_func']

    for func_name, events in [('outer_func', outer_events), ('inner_func', inner_events)]:
        line_events = [e for e in events if e['event'] == 'line']
        if line_events:
            last_event = line_events[-1]
            locals_dict = last_event['locals']
            has_real_names = last_event['has_real_names']

            print(f"\n{func_name}:")
            print(f"  Has real names: {has_real_names}")
            print(f"  Variables: {sorted(locals_dict.keys())}")

            for key, value in sorted(locals_dict.items()):
                print(f"    {key}: {value}")


def test_loop_variables():
    """Test local variable names in loops."""

    def loop_func():
        total = 0
        items = [1, 2, 3]

        for index, value in enumerate(items):
            temp_result = value * 2
            total += temp_result

        return total

    # Clear previous data
    trace_events.clear()

    # Enable tracing and run function
    sys.settrace(trace_handler)
    result = loop_func()
    sys.settrace(None)

    print("\n=== test_loop_variables ===")
    print(f"Function returned: {result}")

    # Find line events in the loop
    loop_events = [
        e for e in trace_events if e['function'] == 'loop_func' and e['event'] == 'line'
    ]

    if loop_events:
        # Check a few different points in execution
        for i, event in enumerate(loop_events[-3:]):  # Last few events
            locals_dict = event['locals']
            has_real_names = event['has_real_names']

            print(f"\n  Event {i} (line {event['lineno']}):")
            print(f"    Has real names: {has_real_names}")
            print(f"    Variables: {sorted(locals_dict.keys())}")


def test_exception_handling():
    """Test local variable names during exception handling."""

    def exception_func():
        dividend = 100
        divisor = 0

        try:
            result = dividend / divisor
        except ZeroDivisionError:
            return -1

        return result

    # Clear previous data
    trace_events.clear()

    # Enable tracing and run function
    sys.settrace(trace_handler)
    result = exception_func()
    sys.settrace(None)

    print("\n=== test_exception_handling ===")
    print(f"Function returned: {result}")

    # Find events in the exception handler
    exception_events = [e for e in trace_events if e['function'] == 'exception_func']
    line_events = [e for e in exception_events if e['event'] == 'line']

    if line_events:
        last_event = line_events[-1]
        locals_dict = last_event['locals']
        has_real_names = last_event['has_real_names']

        print(f"Has real names: {has_real_names}")
        print(f"Variables in exception handler: {sorted(locals_dict.keys())}")

        for key, value in sorted(locals_dict.items()):
            print(f"  {key}: {value}")


def test_complex_types():
    """Test with complex data types."""

    def complex_func():
        user_data = {'name': 'Alice', 'scores': [85, 92, 78], 'active': True}

        coordinates = (10.5, 20.3)

        def process_data():
            return len(user_data['scores'])

        count = process_data()
        return user_data, coordinates, count

    # Clear previous data
    trace_events.clear()

    # Enable tracing and run function
    sys.settrace(trace_handler)
    result = complex_func()
    sys.settrace(None)

    print("\n=== test_complex_types ===")
    print(f"Function returned length: {len(result)}")

    # Analyze the main function
    main_events = [
        e for e in trace_events if e['function'] == 'complex_func' and e['event'] == 'line'
    ]

    if main_events:
        last_event = main_events[-1]
        locals_dict = last_event['locals']
        has_real_names = last_event['has_real_names']

        print(f"Has real names: {has_real_names}")
        print(f"Variables: {sorted(locals_dict.keys())}")

        for key, value in sorted(locals_dict.items()):
            value_str = str(value)
            if len(value_str) > 50:
                value_str = value_str[:47] + "..."
            print(f"  {key}: {value_str} ({type(value).__name__})")


if __name__ == "__main__":
    print("Testing MicroPython local variable name preservation in sys.settrace()")
    print("=" * 70)

    # Run all tests
    test_basic_locals()
    test_nested_functions()
    test_loop_variables()
    test_exception_handling()
    test_complex_types()

    print("\n" + "=" * 70)
    print("Test completed!")

    # Summary
    all_events = trace_events
    events_with_real_names = [e for e in all_events if e.get('has_real_names', False)]

    print(f"Total trace events captured: {len(all_events)}")
    print(f"Events with real variable names: {len(events_with_real_names)}")

    if events_with_real_names:
        print("✓ SUCCESS: Local variable names are being preserved!")
    else:
        print("⚠ FALLBACK: Using index-based names (local_XX)")
        print("This is normal for .mpy files or when LOCALNAMES feature is disabled")
